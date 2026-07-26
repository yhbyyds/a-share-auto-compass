from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

from market_forecast.pipeline import build_forecast
from market_forecast.performance import update_performance_history
from market_forecast.quality import QualityResult, validate_forecast


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "public" / "data" / "forecast.json"
DEFAULT_STATE_DIR = ROOT / "data" / "automation"
DEFAULT_PERFORMANCE = ROOT / "data" / "performance_history.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _find_node() -> str:
    candidates: list[Path] = []
    configured = os.environ.get("ASCOMPASS_NODE")
    if configured:
        candidates.append(Path(configured))

    discovered = shutil.which("node.exe") or shutil.which("node")
    if discovered:
        candidates.append(Path(discovered))

    candidates.append(
        Path(sys.executable).parent
        / "Lib"
        / "site-packages"
        / "playwright"
        / "driver"
        / "node.exe"
    )
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        python_root = Path(local_app_data) / "Programs" / "Python"
        candidates.extend(
            sorted(
                python_root.glob(
                    "Python*/Lib/site-packages/playwright/driver/node.exe"
                ),
                reverse=True,
            )
        )

    checked: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate).lower()
        if candidate_key in checked or not candidate.is_file():
            continue
        checked.add(candidate_key)
        try:
            result = subprocess.run(
                [str(candidate), "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.search(r"v?(\d+)", result.stdout.strip())
        if match and int(match.group(1)) >= 22:
            return str(candidate)

    raise RuntimeError(
        "未找到 Node.js 22 或更高版本；请安装 Node.js 22，"
        "或通过 ASCOMPASS_NODE 指定 node.exe 的完整路径"
    )


def _run_site_build() -> None:
    node_command = _find_node()
    vinext_cli = ROOT / "node_modules" / "vinext" / "dist" / "cli.js"
    encrypt_script = ROOT / "scripts" / "encrypt-forecast.mjs"
    prepare_script = ROOT / "scripts" / "prepare-dist.mjs"
    encrypted_source = ROOT / "public" / "data" / "forecast.enc.json"
    if not vinext_cli.is_file():
        raise RuntimeError("缺少网页构建依赖；请先在项目目录运行 npm install")
    try:
        subprocess.run(
            [node_command, str(encrypt_script)],
            cwd=ROOT,
            check=True,
            text=True,
        )
        subprocess.run(
            [node_command, str(vinext_cli), "build"],
            cwd=ROOT,
            check=True,
            text=True,
        )
        subprocess.run(
            [node_command, str(prepare_script)],
            cwd=ROOT,
            check=True,
            text=True,
        )
    finally:
        encrypted_source.unlink(missing_ok=True)


@contextmanager
def _run_lock(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "update.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("已有自动更新正在运行；本次任务已跳过") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _logger(state_dir: Path) -> logging.Logger:
    state_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("a_share_automation")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    file_handler = RotatingFileHandler(
        state_dir / "automation.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def run_update(
    *,
    output: Path = DEFAULT_OUTPUT,
    state_dir: Path = DEFAULT_STATE_DIR,
    performance_path: Path = DEFAULT_PERFORMANCE,
    max_data_age_days: int = 5,
    dry_run: bool = False,
    run_build: bool = False,
) -> tuple[dict[str, Any], QualityResult]:
    logger = _logger(state_dir)
    started = datetime.now(ZoneInfo("Asia/Shanghai"))
    run_id = started.strftime("%Y%m%dT%H%M%S")
    previous = _read_json(output)
    performance_history = _read_json(performance_path) or {}
    previous_bytes = output.read_bytes() if output.exists() else None

    with _run_lock(state_dir):
        logger.info("自动更新开始 run_id=%s", run_id)
        try:
            forecast = build_forecast()
            updated_performance, performance_monitor = (
                update_performance_history(performance_history, forecast)
            )
            quality = validate_forecast(
                forecast,
                previous=previous,
                today=started.date(),
                max_data_age_days=max_data_age_days,
            )
            forecast["meta"]["automation"] = {
                "run_id": run_id,
                "status": "passed" if quality.passed else "blocked",
                "quality_gate": quality.as_dict(),
                "updated_at": started.isoformat(),
                "mode": "dry-run" if dry_run else "automatic",
            }
            candidate = state_dir / "candidate_forecast.json"
            _atomic_json(candidate, forecast)
            _atomic_json(
                state_dir / "candidate_performance_history.json",
                updated_performance,
            )

            if not quality.passed:
                raise RuntimeError("质量门禁未通过: " + "；".join(quality.errors))

            output_staged = False
            if run_build:
                if output.exists():
                    shutil.copy2(output, state_dir / "previous_forecast.json")
                _atomic_json(output, forecast)
                output_staged = True
                try:
                    _run_site_build()
                except Exception:
                    if previous_bytes is None:
                        output.unlink(missing_ok=True)
                    else:
                        _atomic_bytes(output, previous_bytes)
                    raise

            if dry_run:
                if output_staged:
                    if previous_bytes is None:
                        output.unlink(missing_ok=True)
                    else:
                        _atomic_bytes(output, previous_bytes)
            else:
                if not output_staged:
                    if output.exists():
                        shutil.copy2(output, state_dir / "previous_forecast.json")
                    _atomic_json(output, forecast)
                _atomic_json(performance_path, updated_performance)
                _atomic_json(state_dir / "last_good_forecast.json", forecast)

            finished = datetime.now(ZoneInfo("Asia/Shanghai"))
            state = {
                "run_id": run_id,
                "status": "dry-run" if dry_run else "succeeded",
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "output": str(output),
                "quality_gate": quality.as_dict(),
                "performance_monitor": performance_monitor,
            }
            _atomic_json(state_dir / "last_run.json", state)
            logger.info(
                "自动更新完成 data_through=%s warnings=%s",
                forecast["meta"]["data_through"],
                len(quality.warnings),
            )
            return forecast, quality
        except Exception as exc:
            finished = datetime.now(ZoneInfo("Asia/Shanghai"))
            state = {
                "run_id": run_id,
                "status": "failed",
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "output_preserved": output.exists(),
                "error": str(exc),
            }
            _atomic_json(state_dir / "last_run.json", state)
            logger.exception("自动更新失败；线上候选文件未覆盖")
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="A股罗盘自动更新与质量门禁")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument(
        "--performance-path",
        type=Path,
        default=DEFAULT_PERFORMANCE,
    )
    parser.add_argument("--max-data-age-days", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    try:
        forecast, quality = run_update(
            output=args.output,
            state_dir=args.state_dir,
            performance_path=args.performance_path,
            max_data_age_days=args.max_data_age_days,
            dry_run=args.dry_run,
            run_build=args.build,
        )
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(
        f"OK release={forecast['meta']['release']} "
        f"data_through={forecast['meta']['data_through']} "
        f"warnings={len(quality.warnings)}"
    )


if __name__ == "__main__":
    main()
