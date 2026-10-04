import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

const spoqaHanSansNeo = localFont({
  src: [
    { path: "./fonts/SpoqaHanSansNeo-Thin.woff2", weight: "100" },
    { path: "./fonts/SpoqaHanSansNeo-Light.woff2", weight: "300" },
    { path: "./fonts/SpoqaHanSansNeo-Regular.woff2", weight: "400" },
    { path: "./fonts/SpoqaHanSansNeo-Medium.woff2", weight: "500" },
    { path: "./fonts/SpoqaHanSansNeo-Bold.woff2", weight: "700" },
  ],
  variable: "--font-spoqa-han-sans-neo",
});

export const metadata: Metadata = {
  title: "AI Monitoring Dashboard",
  description: "",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${spoqaHanSansNeo.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
