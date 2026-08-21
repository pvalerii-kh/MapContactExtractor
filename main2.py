import customtkinter as ctk
import asyncio
import threading
import re
import csv
from playwright.async_api import async_playwright
from tkinter import filedialog

ctk.set_appearance_mode("light") 
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Google Maps Website Scraper Pro")
        self.geometry("750x700")
        self.resizable(False, False)
        self.configure(fg_color="#F0F2F5")

        self.is_running = False
        self.results_data = []

        self.label = ctk.CTkLabel(self, text="Web Contact Extractor", font=("Segoe UI", 24, "bold"), text_color="#1A1A1A")
        self.label.pack(pady=(25, 5))
        
        self.textbox = ctk.CTkTextbox(self, width=650, height=120, corner_radius=15, border_width=2, border_color="#E4E6EB", fg_color="#FFFFFF", font=("Consolas", 12))
        self.textbox.pack(pady=10)

        self.btn_paste = ctk.CTkButton(self, text="📋 Вставить из буфера", command=self.force_paste, fg_color="#E4E6EB", text_color="#1C1E21", hover_color="#D8DADF", corner_radius=10)
        self.btn_paste.pack(pady=5)

        self.frame_controls = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_controls.pack(pady=15)

        self.btn_start = ctk.CTkButton(self.frame_controls, text="🚀 НАЧАТЬ", command=self.start_thread, fg_color="#0084FF", hover_color="#0073E6", width=160, height=40, corner_radius=12, font=("Segoe UI", 14, "bold"))
        self.btn_start.grid(row=0, column=0, padx=12)

        self.btn_stop = ctk.CTkButton(self.frame_controls, text="🛑 СТОП", command=self.stop_scraping, fg_color="#FA3E3E", hover_color="#E03535", width=160, height=40, corner_radius=12, font=("Segoe UI", 14, "bold"))
        self.btn_stop.grid(row=0, column=1, padx=12)

        self.log_box = ctk.CTkTextbox(self, width=650, height=220, corner_radius=15, border_width=2, border_color="#E4E6EB", fg_color="#F7F8FA", font=("Consolas", 12), text_color="#333333")
        self.log_box.pack(pady=10)

        self.btn_save = ctk.CTkButton(self, text="💾 Сохранить в CSV", command=self.save_to_csv, fg_color="#42B72A", hover_color="#36A420", width=250, height=45, corner_radius=12, font=("Segoe UI", 14, "bold"))
        self.btn_save.pack(pady=15)

    def force_paste(self):
        try:
            text = self.clipboard_get()
            if text: self.textbox.insert("end", text + "\n")
        except: pass

    def stop_scraping(self):
        self.is_running = False
        self.log("⛔ Запрос на остановку... Новые вкладки не откроются.")

    def log(self, text):
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    async def process_url(self, url, context, semaphore, social_junk):
        """Функция для обработки одного URL внутри семафора"""
        async with semaphore:
            if not self.is_running: return
            
            url = url.strip()
            if not url: return
            if any(x in url.lower() for x in ["instagram.com", "facebook.com", "linkedin.com", "t.me"]):
                self.results_data.append({"url": url, "emails": "Соцсеть"})
                return

            if not url.startswith('http'): url = 'http://' + url
            self.log(f"🔎 Проверяю: {url}")
            
            page = await context.new_page()
            status = "Ошибка"
            
            try:
                # Настройка: ждем загрузки и прокручиваем
                await page.goto(url, timeout=45000, wait_until="load")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.5)
                
                content = await page.content()
                raw_emails = set(re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', content))
                valid_emails = [e for e in raw_emails if not any(ext in e.lower() for ext in ['.png', '.jpg', '.svg', '.gif', '.ico', '.webp']) and not any(junk in e.lower() for junk in social_junk)]
                
                if valid_emails:
                    status = ", ".join(valid_emails)
                    self.log(f"✅ НАЙДЕНО ({url}): {status}")
                else:
                    self.log(f"❌ Нет почты на: {url}")
                    status = "Почта не найдена"
            except Exception as e:
                status = str(e).split('\n')[0]
                self.log(f"⚠️ Ошибка на {url}: {status}")
            
            self.results_data.append({"url": url, "emails": status})
            await page.close()

    async def scrape_logic(self, urls):
        self.is_running = True
        self.results_data = []
        social_junk = ["facebook", "instagram", "youtube", "twitter", "pinterest", "linkedin", "telegram", "vimeo", "google"]
        
        # Ограничение: 3 параллельных вкладки
        semaphore = asyncio.Semaphore(3)
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            
            # Создаем список задач
            tasks = [self.process_url(url, context, semaphore, social_junk) for url in urls]
            
            # Запускаем все задачи
            await asyncio.gather(*tasks)
                
            await browser.close()
            self.is_running = False
            self.log("🏁 ВСЕ САЙТЫ ОБРАБОТАНЫ!")

    def start_thread(self):
        if self.is_running: return
        urls = self.textbox.get("1.0", "end-1c").split("\n")
        threading.Thread(target=lambda: asyncio.run(self.scrape_logic(urls)), daemon=True).start()

    def save_to_csv(self):
        if not self.results_data: return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if path:
            with open(path, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=["url", "emails"])
                writer.writeheader()
                writer.writerows(self.results_data)
            self.log(f"💾 Файл сохранен: {path}")

if __name__ == "__main__":
    app = App()
    app.mainloop()