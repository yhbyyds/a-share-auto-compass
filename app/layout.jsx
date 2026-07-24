export const metadata = {
  title: "A股下周罗盘",
  description: "基于滚动样本外验证的A股下周大盘方向研究系统",
};

export default function RootLayout({ children }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

