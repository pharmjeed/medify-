import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Medify | التوثيق السريري الذكي",
  description: "من الاستشارة العربية إلى ملخص SOAP معتمد وجاهز للرفع",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ar" dir="rtl">
      <body>{children}</body>
    </html>
  );
}

