import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { cn } from "@/lib/utils";

const maruBuri = localFont({
  src: [
    { path: "./fonts/MaruBuri-ExtraLight.woff2", weight: "200" },
    { path: "./fonts/MaruBuri-Light.woff2", weight: "300" },
    { path: "./fonts/MaruBuri-Regular.woff2", weight: "400" },
    { path: "./fonts/MaruBuri-SemiBold.woff2", weight: "600" },
    { path: "./fonts/MaruBuri-Bold.woff2", weight: "700" },
  ],
  variable: "--font-maru-buri",
});

export const metadata: Metadata = {
  title: "AI Monitoring Dashboard",
  description: "",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={cn("h-full", "antialiased", "font-serif", maruBuri.variable)}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
