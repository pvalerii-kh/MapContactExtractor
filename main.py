import customtkinter as ctk
import asyncio
import threading
import re
import csv
from playwright.async_api import async_playwright
from tkinter import filedialog

# --- СОВРЕМЕННЫЙ ДИЗАЙН (СВЕТЛАЯ ТЕМА + ГЛАССМОРФИЗМ) ---
ctk.set_appearance_mode("light") 
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Website Scraper")
        self.geometry("750x700")
        self.configure(fg_color="#F0F2F5") # Светлый фоновый цвет (Facebook-style)
        self.resizable(False, False)

        self.is_running = False
        self.results_data = []

        # Заголовок
        self.label = ctk.CTkLabel(self, text="Web Contact Extractor", font=("Segoe UI", 24, "bold"), text_color="#1A1A1A")
        self.label.pack(pady=(25, 5))
        
        self.sub_label = ctk.CTkLabel(self, text="Извлечение почты с эффектом прозрачности", font=("Segoe UI", 12), text_color="#606770")
        self.sub_label.pack(pady=(0, 15))

        # Поле ввода (Стеклянный эффект)
        self.textbox = ctk.CTkTextbox(self, width=650, height=120, 
                                     corner_radius=15, 
                                     border_width=2,
                                     border_color="#E4E6EB",
                                     fg_color="#FFFFFF",
                                     font=("Consolas", 12))
        self.textbox.pack(pady=10)

        # Кнопка вставки
        self.btn_paste = ctk.CTkButton(self, text="📋 Вставить из буфера", 
                                       command=self.force_paste, 
                                       fg_color="#E4E6EB", 
                                       text_color="#1C1E21",
                                       hover_color="#D8DADF",
                                       corner_radius=10)
        self.btn_paste.pack(pady=5)

        # Контейнер для кнопок управления
        self.frame_controls = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_controls.pack(pady=15)

        self.btn_start = ctk.CTkButton(self.frame_controls, text="🚀 НАЧАТЬ", 
                                       command=self.start_thread, 
                                       fg_color="#0084FF", 
                                       hover_color="#0073E6",
                                       width=160, height=40,
                                       corner_radius=12,
                                       font=("Segoe UI", 14, "bold"))
        self.btn_start.grid(row=0, column=0, padx=12)

        self.btn_stop = ctk.CTkButton(self.frame_controls, text="🛑 СТОП", 
                                      command=self.stop_scraping, 
                                      fg_color="#FA3E3E", 
                                      hover_color="#E03535",
                                      width=160, height=40,
                                      corner_radius=12,
                                      font=("Segoe UI", 14, "bold"))
        self.btn_stop.grid(row=0, column=1, padx=12)

        # Окно логов (Стеклянный эффект)
        self.log_box = ctk.CTkTextbox(self, width=650, height=220, 
                                     corner_radius=15,
                                     border_width=2,
                                     border_color="#E4E6EB",
                                     fg_color="#F7F8FA",
                                     font=("Consolas", 12),
                                     text_color="#333333")
        self.log_box.pack(pady=10)

        # Кнопка сохранения
        self.btn_save = ctk.CTkButton(self, text="💾 Сохранить в Excel/CSV", 
                                      command=self.save_to_csv, 
                                      fg_color="#42B72A", 
                                      hover_color="#36A420",
                                      width=250, height=45,
                                      corner_radius=12,
                                      font=("Segoe UI", 14, "bold"))
        self.btn_save.pack(pady=15)

    # --- ЛОГИКА ОСТАЕТСЯ ПРЕЖНЕЙ (БЕЗ ИЗМЕНЕНИЙ) ---

    def force_paste(self):
        try:
            text = self.clipboard_get()
            if text: self.textbox.insert("end", text + "\n")
        except: pass

    def stop_scraping(self):
        if self.is_running:
            self.is_running = False
            self.log("⛔ Остановка...")

    def log(self, text):
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    async def scroll_down(self, page):
        await page.evaluate("""async () => { await new Promise((resolve) => { let totalHeight = 0; let distance = 100; let timer = setInterval(() => { let scrollHeight = document.body.scrollHeight; window.scrollBy(0, distance); totalHeight += distance; if(totalHeight >= scrollHeight){ clearInterval(timer); resolve(); } }, 100); }); }""")

    async def scrape_logic(self, urls):
        self.is_running = True
        self.results_data = []
        social_junk = ["facebook", "instagram", "youtube", "twitter", "pinterest", "linkedin", "telegram", "vimeo", "google"]
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")
                page = await context.new_page()
                for url in urls:
                    if not self.is_running: break
                    url = url.strip()
                    if not url: continue
                    if any(x in url.lower() for x in ["instagram.com", "facebook.com", "linkedin.com", "t.me"]):
                        self.log(f"📲 Соцсеть пропущена: {url}")
                        self.results_data.append({"url": url, "emails": "Соцсеть"})
                        continue
                    if not url.startswith('http'): url = 'http://' + url
                    self.log(f"🔎 Поиск: {url}")
                    status = "Не обработан"
                    try:
                        response = await page.goto(url, timeout=40000, wait_until="domcontentloaded")
                        if response and response.status < 400:
                            await self.scroll_down(page)
                            await asyncio.sleep(1)
                            content = await page.content()
                            raw_emails = set(re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', content))
                            valid_emails = [e for e in raw_emails if not any(ext in e.lower() for ext in ['.png', '.jpg', '.svg', '.gif', '.ico', '.webp']) and not any(junk in e.lower() for junk in social_junk)]
                            if valid_emails:
                                found = ", ".join(valid_emails)
                                self.log(f"✅ НАЙДЕНО: {found}")
                                status = found
                            else:
                                self.log("❌ Почты нет")
                                status = "Почта не найдена"
                        else:
                            status = f"Ошибка доступа {response.status if response else ''}"
                            self.log(f"⚠️ {status}")
                    except Exception as e:
                        status = str(e).split('\n')[0]
                        self.log(f"⚠️ {status}")
                    self.results_data.append({"url": url, "emails": status})
                    self.log("-" * 35)
                await browser.close()
                self.is_running = False
                self.log("🏁 ГОТОВО!")
            except Exception as e:
                self.log(f"🔴 Ошибка: {e}")
                self.is_running = False

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
            self.log(f"💾 Сохранено: {path}")

if __name__ == "__main__":
    app = App()
    app.mainloop()