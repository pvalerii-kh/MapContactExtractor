import atexit
import asyncio
import csv
import json
import queue
import re
import sys
import threading
from pathlib import Path
from tkinter import filedialog
from urllib.parse import unquote, urlparse

import customtkinter as ctk
from playwright.async_api import async_playwright

def runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


SESSION_PATH = runtime_dir() / "session.json"

BG = "#07090F"
CARD = "#141824"
CARD_BORDER = "#2E3A52"
INPUT = "#0C1018"
INPUT_BORDER = "#33405C"
TEXT = "#E8ECF4"
MUTED = "#8B95A8"
ACCENT = "#7C8CFF"
ACCENT_HOVER = "#6A78F0"
TEAL = "#2EC4B6"
TEAL_HOVER = "#26A99D"
DANGER = "#E85D75"
DANGER_HOVER = "#D14B63"
GHOST = "#1A2233"
GHOST_HOVER = "#243049"
TRACK = "#0C1018"

URL_KEYS = {
    "url", "urls", "website", "web", "site", "link", "domain",
    "сайт", "домен", "ссылка", "веб",
}
DOMAIN_RE = re.compile(
    r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}([/?#].*)?$",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9](?:[a-zA-Z0-9._%+-]*[a-zA-Z0-9])?@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*"
    r"\.[a-zA-Z]{2,}"
)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".ico", ".webp")
SOCIAL_HOSTS = ("instagram.com", "facebook.com", "linkedin.com", "t.me")
SOCIAL_JUNK = (
    "facebook", "instagram", "youtube", "twitter", "pinterest",
    "linkedin", "telegram", "vimeo", "google",
)


def normalize(url: str) -> str:
    value = url.strip().lower()
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return value.rstrip("/")


def looks_like_site(value: str) -> bool:
    candidate = value.strip().strip("\"'")
    if not candidate or " " in candidate:
        return False
    return DOMAIN_RE.match(candidate) is not None


def clean_email(raw: str) -> str | None:
    match = EMAIL_RE.search(raw or "")
    if not match:
        return None
    email = match.group(0).lower()
    if any(ext in email for ext in IMAGE_EXT):
        return None
    if any(junk in email for junk in SOCIAL_JUNK):
        return None
    return email


def extract_emails(results) -> list[str]:
    found = []
    seen = set()
    for item in results:
        text = item.get("emails", "") if isinstance(item, dict) else ""
        for email in EMAIL_RE.findall(text or ""):
            cleaned = clean_email(email)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            found.append(cleaned)
    return found


def unique_sites(lines) -> list[str]:
    seen = set()
    result = []
    for line in lines:
        raw = line.strip().strip("\"'")
        if not raw or raw.startswith("#"):
            continue
        key = normalize(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(raw)
    return result


def sites_from_csv(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(handle, dialect))

    if not rows:
        return []

    header = [cell.strip().lower() for cell in rows[0]]
    column = next((index for index, name in enumerate(header) if name in URL_KEYS), None)
    if column is not None:
        values = [row[column] for row in rows[1:] if len(row) > column]
        return unique_sites(values)

    values = []
    for row in rows:
        for cell in row:
            if looks_like_site(cell):
                values.append(cell)
    return unique_sites(values)


SOCIAL_DOMAINS = (
    "facebook.com", "fb.com", "instagram.com", "linkedin.com",
    "t.me", "telegram.me", "tiktok.com", "youtube.com", "youtu.be",
    "twitter.com", "x.com", "vk.com", "ok.ru", "pinterest.com",
    "wa.me", "whatsapp.com",
)


def is_social_site(url: str) -> bool:
    host = urlparse(url if "://" in url else "http://" + url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == domain or host.endswith("." + domain) for domain in SOCIAL_DOMAINS)


CONTACT_FIELDS = [
    "Name", "Address", "Website", "Website2",
    "Phone1", "Phone2", "Phone3",
    "Email1", "Email2", "Email3",
    "Category", "Signals", "MapsUrl",
]
PHONE_SLOTS = ("Phone1", "Phone2", "Phone3")
EMAIL_SLOTS = ("Email1", "Email2", "Email3")
SITE_SLOTS = ("Website", "Website2")
FIELD_ALIASES = {
    "Name": ("name", "название", "title"),
    "Address": ("address", "адрес"),
    "Website": ("website", "web", "site", "url", "сайт"),
    "Website2": ("website2", "website 2", "web2", "site2", "url2"),
    "Phone1": ("phone1", "phone", "телефон", "tel"),
    "Phone2": ("phone2",),
    "Phone3": ("phone3",),
    "Email1": ("email1", "email", "emails", "почта", "mail", "e-mail"),
    "Email2": ("email2",),
    "Email3": ("email3",),
    "Category": ("category", "категория"),
    "Signals": ("signals",),
    "MapsUrl": ("mapsurl", "maps url", "maps"),
}
TEL_HREF_RE = re.compile(r"(?i)tel:\s*([^\"'<>\s]+)")
PLUS_PHONE_RE = re.compile(r"(?<!\w)\+\d{1,3}(?:[\s().-]*\d){8,14}(?!\d)")
LOCAL_PHONE_RE = re.compile(r"(?<!\d)\d{3}[\s.-]\d{3}[\s.-]\d{3}(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)\d{2}[\s.-]\d{3}[\s.-]\d{2}[\s.-]\d{2}(?!\d)")


def column_name(fields: dict, keys) -> str | None:
    return next((fields[key] for key in keys if key in fields), None)


def split_values(value: str) -> list[str]:
    if not value or not value.strip():
        return []
    return [part.strip() for part in re.split(r"\s*[,;|]\s*", value.strip()) if part.strip()]


def phone_digits(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 9:
        digits = "48" + digits
    return digits


def format_phone(raw: str) -> str | None:
    digits = phone_digits(raw)
    if not digits or len(digits) < 10 or len(digits) > 15 or len(set(digits)) <= 2:
        return None
    if digits.startswith("48") and len(digits) == 11:
        rest = digits[2:]
        return f"+48 {rest[:3]} {rest[3:6]} {rest[6:]}"
    return "+" + digits


def extract_phones(html: str) -> list[str]:
    visible = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    found = []
    seen = set()

    def add(raw: str):
        formatted = format_phone(unquote(raw))
        if not formatted:
            return
        key = phone_digits(formatted)
        if key in seen:
            return
        seen.add(key)
        found.append(formatted)

    for match in TEL_HREF_RE.findall(html):
        add(match)
    for match in PLUS_PHONE_RE.findall(visible):
        add(match)
    for match in LOCAL_PHONE_RE.findall(visible):
        add(match)
    for match in LANDLINE_RE.findall(visible):
        add(match)
    return found


def read_alias(row: dict, fields: dict, aliases) -> str:
    for alias in aliases:
        name = fields.get(alias)
        if name:
            return (row.get(name) or "").strip()
    return ""


def assign_slots(values, slots, key_fn) -> dict:
    unique = []
    seen = set()
    for value in values:
        text = value.strip()
        key = key_fn(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        unique.append(text)
    assigned = {slot: "" for slot in slots}
    if unique:
        assigned[slots[0]] = unique[0]
    if len(unique) > 1:
        assigned[slots[1]] = unique[1]
    if len(unique) > 2:
        assigned[slots[2]] = ", ".join(unique[2:])
    return assigned


def slot_values(row: dict, slots) -> list[str]:
    values = []
    for slot in slots:
        values.extend(split_values(row.get(slot, "")))
    return values


def merge_slots(row: dict, slots, new_values, key_fn) -> bool:
    current = slot_values(row, slots)
    seen = {key_fn(value) for value in current}
    added = False
    for value in new_values:
        text = value.strip()
        key = key_fn(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        current.append(text)
        added = True
    if added:
        row.update(assign_slots(current, slots, key_fn))
    return added


def row_has_email(row: dict) -> bool:
    return any(split_values(row.get(slot, "")) for slot in EMAIL_SLOTS)


def row_has_phone(row: dict) -> bool:
    return any(split_values(row.get(slot, "")) for slot in PHONE_SLOTS)


def row_is_complete(row: dict, deep: bool = False) -> bool:
    if deep:
        return all((row.get(slot) or "").strip() for slot in PHONE_SLOTS + EMAIL_SLOTS)
    return row_has_email(row) and row_has_phone(row)


CONTACT_HINTS = ("kontakt", "contact", "impressum", "imprint", "контак")
STRONG_SEGMENTS = (
    "kontakt", "kontakty", "contact", "contacts", "impressum", "imprint",
    "skontaktuj", "contacto", "contatti", "contato", "контакты", "контакт",
)
CONTACT_PAGE_LIMIT = 2


def strong_contact_path(path: str) -> bool:
    for segment in path.split("/"):
        token = segment.strip().lower()
        if not token:
            continue
        if token in STRONG_SEGMENTS:
            return True
        if any(token.startswith(item + "-") or token.startswith(item + "_") for item in STRONG_SEGMENTS):
            return True
    return False
FILE_EXT = (
    ".pdf", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".ico", ".webp",
    ".zip", ".rar", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)


def page_contacts(html: str) -> tuple[list[str], list[str]]:
    emails = []
    seen = set()
    for raw in EMAIL_RE.findall(html or ""):
        email = clean_email(raw)
        if not email or email in seen:
            continue
        seen.add(email)
        emails.append(email)
    return emails, extract_phones(html or "")


def merge_found(emails: list[str], phones: list[str], more_emails, more_phones) -> None:
    seen_emails = {item.lower() for item in emails}
    for email in more_emails:
        key = email.lower()
        if key in seen_emails:
            continue
        seen_emails.add(key)
        emails.append(email)
    seen_phones = {phone_digits(item) for item in phones}
    for phone in more_phones:
        key = phone_digits(phone)
        if not key or key in seen_phones:
            continue
        seen_phones.add(key)
        phones.append(phone)


def site_host(url: str) -> str:
    host = urlparse(url if "://" in url else "http://" + url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]


def contact_links(page_url: str, links) -> list[str]:
    """Ссылки на страницы контактов того же сайта, самые явные — первыми."""
    home = normalize(page_url)
    home_host = site_host(page_url)
    scored = []
    seen = set()
    for item in links or []:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "").strip()
        text = str(item.get("text") or "").strip().lower()
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https") or not home_host:
            continue
        if site_host(href) != home_host or is_social_site(href):
            continue
        path = unquote(parsed.path or "").lower()
        if any(path.endswith(ext) for ext in FILE_EXT):
            continue
        blob = f"{path} {text}"
        if not any(hint in blob for hint in CONTACT_HINTS):
            continue
        query = parsed.query if path in ("", "/") else ""
        clean = parsed._replace(query=query, fragment="").geturl()
        key = normalize(clean)
        if not key or key == home or key in seen:
            continue
        seen.add(key)
        score = 0
        if strong_contact_path(path) or strong_contact_path(unquote(query)):
            score += 4
        if any(hint in path or hint in unquote(query).lower() for hint in CONTACT_HINTS):
            score += 2
        if any(hint in text for hint in CONTACT_HINTS):
            score += 1
        scored.append((-score, len(path), clean))
    scored.sort()
    return [url for _, _, url in scored[:CONTACT_PAGE_LIMIT]]


def load_contact_table(path: Path) -> dict:
    """Таблица с фиксированными столбцами контактов."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("В файле нет строки заголовков")
        fieldnames = [name for name in reader.fieldnames if name and name.strip()]
        fields = {name.strip().lower(): name for name in fieldnames}
        has_site = any(column_name(fields, FIELD_ALIASES[slot]) for slot in SITE_SLOTS)
        if not has_site:
            raise ValueError("Нужна колонка Website")
        rows = []
        for source in reader:
            if not source:
                continue
            row = {name: "" for name in CONTACT_FIELDS}
            for name, aliases in FIELD_ALIASES.items():
                row[name] = read_alias(source, fields, aliases)
            emails = []
            for slot in EMAIL_SLOTS:
                for part in split_values(row[slot]):
                    email = clean_email(part)
                    if email:
                        emails.append(email)
            phones = []
            for slot in PHONE_SLOTS:
                phones.extend(split_values(row[slot]))
            row.update(assign_slots(emails, EMAIL_SLOTS, str.lower))
            row.update(assign_slots(phones, PHONE_SLOTS, phone_digits))
            rows.append(row)
    return {
        "path": path,
        "fieldnames": list(CONTACT_FIELDS),
        "rows": rows,
    }


def row_sites(row: dict) -> list[str]:
    sites = []
    seen = set()
    for slot in SITE_SLOTS:
        site = (row.get(slot) or "").strip()
        key = normalize(site)
        if not key or key in seen:
            continue
        seen.add(key)
        sites.append(site)
    return sites


def summarize_contact_table(table: dict, deep: bool = False) -> tuple[list[str], int, int, int]:
    sites = []
    ready = 0
    without_site = 0
    social = 0
    for row in table["rows"]:
        found = row_sites(row)
        if not found:
            without_site += 1
            continue
        usable = [site for site in found if not is_social_site(site)]
        if not usable:
            social += 1
            continue
        if row_is_complete(row, deep):
            ready += 1
            continue
        sites.extend(usable)
    return unique_sites(sites), ready, without_site, social


def write_contact_table(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") or "" for name in fieldnames})
    temporary.replace(path)


def sites_without_email(path: Path) -> tuple[list[str], int, int, int]:
    """Сайты из строк, где не хватает контактов, а Website или Website2 заполнены."""
    return summarize_contact_table(load_contact_table(path))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Contact Extractor")
        self.geometry("1100x920")
        self.minsize(980, 820)
        self.configure(fg_color=BG)

        self.is_running = False
        self.results_data = []
        self._done_norms = set()
        self._session_urls = []
        self._state_lock = threading.Lock()
        self._ui_queue = queue.Queue()
        self._loop = None
        self._browser = None
        self._tasks = []
        self._table = None
        self._deep = False
        self._restoring = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(4, weight=2)

        self._build_header()
        self._build_sites_card()
        self._build_settings_card()
        self._build_actions()
        self._build_log_card()
        self._build_save()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        atexit.register(self._save_on_exit)
        self._restore_session()
        self._sync_buttons()
        self.after(100, self._pump_ui)

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 8))

        ctk.CTkLabel(
            header, text="Contact Extractor",
            font=("Segoe UI", 28, "bold"), text_color=TEXT, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Параллельный сбор почты и телефонов с сайтов",
            font=("Segoe UI", 14), text_color=MUTED, anchor="w",
        ).pack(anchor="w", pady=(2, 0))

    def _card(self, row, pady):
        frame = ctk.CTkFrame(
            self, fg_color=CARD, corner_radius=22,
            border_width=1, border_color=CARD_BORDER,
        )
        frame.grid(row=row, column=0, sticky="nsew", padx=28, pady=pady)
        return frame

    def _build_sites_card(self):
        card = self._card(1, (8, 8))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="Список сайтов",
            font=("Segoe UI", 15, "bold"), text_color=TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 8))

        self.textbox = ctk.CTkTextbox(
            card, corner_radius=16, border_width=1, border_color=INPUT_BORDER,
            fg_color=INPUT, text_color=TEXT, font=("Consolas", 13),
        )
        self.textbox.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))

        tools = ctk.CTkFrame(card, fg_color="transparent")
        tools.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 14))

        self.btn_paste = self._ghost_button(tools, "Вставить из буфера", self.force_paste)
        self.btn_paste.pack(side="left", padx=(0, 8))
        self.btn_load = self._ghost_button(tools, "Загрузить файл", self.load_from_file)
        self.btn_load.pack(side="left", padx=(0, 8))
        self.btn_load_missing = self._ghost_button(tools, "Сайты без почты", self.load_sites_without_email)
        self.btn_load_missing.pack(side="left", padx=(0, 8))
        self.btn_clear = self._ghost_button(tools, "Очистить", self.clear_list)
        self.btn_clear.pack(side="left")

    def _build_settings_card(self):
        card = self._card(2, (0, 8))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 0))
        ctk.CTkLabel(
            top, text="Одновременных потоков",
            font=("Segoe UI", 15, "bold"), text_color=TEXT,
        ).pack(side="left")
        self.workers_label = ctk.CTkLabel(
            top, text="3", font=("Segoe UI", 20, "bold"), text_color=ACCENT,
        )
        self.workers_label.pack(side="right")

        self.slider = ctk.CTkSlider(
            card, from_=1, to=5, number_of_steps=4, command=self._on_workers,
            progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            fg_color=TRACK,
        )
        self.slider.set(3)
        self.slider.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 4))

        ctk.CTkLabel(
            card, text="Прогресс",
            font=("Segoe UI", 13), text_color=MUTED, anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(8, 0))

        self.progress = ctk.CTkProgressBar(
            card, height=12, corner_radius=8,
            progress_color=ACCENT, fg_color=TRACK,
        )
        self.progress.set(0)
        self.progress.grid(row=3, column=0, sticky="ew", padx=18, pady=(6, 4))

        self.progress_label = ctk.CTkLabel(
            card, text="0 / 0   ·   0%",
            font=("Segoe UI", 13), text_color=MUTED, anchor="w",
        )
        self.progress_label.grid(row=4, column=0, sticky="w", padx=18, pady=(0, 4))

        self.deep_var = ctk.BooleanVar(value=False)
        self.deep_check = ctk.CTkCheckBox(
            card,
            text="Глубокий анализ",
            variable=self.deep_var,
            command=self._on_deep_toggle,
            font=("Segoe UI", 16, "bold"),
            text_color="#FFFFFF",
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            border_color=INPUT_BORDER,
            checkmark_color="#FFFFFF",
        )
        self.deep_check.grid(row=5, column=0, sticky="w", padx=18, pady=(10, 0))

        ctk.CTkLabel(
            card,
            text=(
                "В очередь попадает строка, если пуст хотя бы один из трёх телефонов "
                "или одна из трёх почт. Дополнительно открывается страница «Контакты»."
            ),
            font=("Segoe UI", 13), text_color=MUTED, anchor="w", justify="left", wraplength=980,
        ).grid(row=6, column=0, sticky="ew", padx=18, pady=(4, 14))

    def _build_actions(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=28, pady=(4, 8))
        row.grid_columnconfigure((0, 1, 2), weight=1)

        self.btn_start = ctk.CTkButton(
            row, text="Начать", command=self.start_fresh,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
            text_color_disabled="#FFFFFF",
            height=52, corner_radius=14, font=("Segoe UI", 18, "bold"),
        )
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.btn_resume = ctk.CTkButton(
            row, text="Продолжить", command=self.resume,
            fg_color=TEAL, hover_color=TEAL_HOVER, text_color="#FFFFFF",
            text_color_disabled="#FFFFFF",
            height=52, corner_radius=14, font=("Segoe UI", 18, "bold"),
        )
        self.btn_resume.grid(row=0, column=1, sticky="ew", padx=8)

        self.btn_stop = ctk.CTkButton(
            row, text="Стоп", command=self.stop_scraping,
            fg_color=DANGER, hover_color=DANGER_HOVER, text_color="#FFFFFF",
            text_color_disabled="#FFFFFF",
            height=52, corner_radius=14, font=("Segoe UI", 18, "bold"),
        )
        self.btn_stop.grid(row=0, column=2, sticky="ew", padx=(8, 0))

    def _build_log_card(self):
        card = self._card(4, (0, 8))
        card.grid_columnconfigure(0, weight=7)
        card.grid_columnconfigure(1, weight=3)
        card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="Журнал",
            font=("Segoe UI", 15, "bold"), text_color=TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(18, 8), pady=(14, 8))

        self.email_count_label = ctk.CTkLabel(
            card, text="Собрано · 0",
            font=("Segoe UI", 15, "bold"), text_color=TEXT, anchor="w",
        )
        self.email_count_label.grid(row=0, column=1, sticky="w", padx=(8, 18), pady=(14, 8))

        self.log_box = ctk.CTkTextbox(
            card, width=10, corner_radius=16, border_width=1, border_color=INPUT_BORDER,
            fg_color=INPUT, text_color="#C9D2E3", font=("Consolas", 12),
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=(18, 8), pady=(0, 16))
        self.log_box.bind("<Key>", lambda _event: "break")

        self.email_box = ctk.CTkTextbox(
            card, width=10, corner_radius=16, border_width=1, border_color=INPUT_BORDER,
            fg_color=INPUT, text_color="#D6F5E8", font=("Consolas", 12),
        )
        self.email_box.grid(row=1, column=1, sticky="nsew", padx=(8, 18), pady=(0, 16))
        self.email_box.bind("<Key>", lambda _event: "break")

    def _build_save(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=5, column=0, sticky="ew", padx=28, pady=(0, 22))
        row.grid_columnconfigure((0, 1), weight=1)

        self.btn_save_session = ctk.CTkButton(
            row, text="Сохранить сессию", command=self.save_session_now,
            fg_color=GHOST, hover_color=GHOST_HOVER, text_color="#FFFFFF",
            text_color_disabled="#FFFFFF",
            border_width=1, border_color=CARD_BORDER,
            height=50, corner_radius=14, font=("Segoe UI", 17, "bold"),
        )
        self.btn_save_session.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.btn_save = ctk.CTkButton(
            row, text="Сохранить в CSV", command=self.save_to_csv,
            fg_color=GHOST, hover_color=GHOST_HOVER, text_color="#FFFFFF",
            text_color_disabled="#FFFFFF",
            border_width=1, border_color=CARD_BORDER,
            height=50, corner_radius=14, font=("Segoe UI", 17, "bold"),
        )
        self.btn_save.grid(row=0, column=1, sticky="ew", padx=(8, 0))

    def _ghost_button(self, parent, text, command):
        return ctk.CTkButton(
            parent, text=text, command=command,
            fg_color=GHOST, hover_color=GHOST_HOVER, text_color="#FFFFFF",
            text_color_disabled="#FFFFFF",
            border_width=1, border_color=INPUT_BORDER,
            height=40, corner_radius=12, font=("Segoe UI", 16, "bold"),
        )

    def _on_workers(self, value):
        self.workers_label.configure(text=str(int(round(value))))

    def workers(self) -> int:
        return max(1, min(5, int(round(self.slider.get()))))

    def _append_log(self, text):
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    def _apply_progress(self, done, total):
        fraction = 0 if total == 0 else done / total
        self.progress.set(fraction)
        self.progress_label.configure(
            text=f"{done} / {total}   ·   {int(fraction * 100)}%"
        )

    def _sync_email_panel(self):
        with self._state_lock:
            emails = extract_emails(self.results_data)
        self.email_box.delete("1.0", "end")
        if emails:
            self.email_box.insert("1.0", "\n".join(emails))
            self.email_box.see("end")
        self.email_count_label.configure(text=f"Собрано · {len(emails)}")

    def log(self, text):
        if threading.current_thread() is threading.main_thread():
            try:
                self._append_log(text)
            except Exception:
                pass
            return
        self._ui_queue.put(("log", text))

    def _set_progress(self, done, total):
        if threading.current_thread() is threading.main_thread():
            try:
                self._apply_progress(done, total)
            except Exception:
                pass
            return
        self._ui_queue.put(("progress", (done, total)))

    def _pump_ui(self):
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    self._apply_progress(*payload)
                elif kind == "buttons":
                    self._sync_buttons()
                elif kind == "emails":
                    self._sync_email_panel()
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            if self.winfo_exists():
                self.after(100, self._pump_ui)
        except Exception:
            pass

    def _urls_from_box(self) -> list[str]:
        return unique_sites(self.textbox.get("1.0", "end-1c").splitlines())

    def _fill_textbox(self, urls):
        self.textbox.delete("1.0", "end")
        if urls:
            self.textbox.insert("1.0", "\n".join(urls))

    def force_paste(self):
        try:
            text = self.clipboard_get()
        except Exception:
            return
        if not text:
            return
        incoming = unique_sites(text.splitlines())
        if not incoming:
            return
        merged = unique_sites(self._urls_from_box() + incoming)
        self._fill_textbox(merged)
        self._refresh_progress_from_state()
        self.log(f"Из буфера добавлено адресов: {len(incoming)}. Всего: {len(merged)}.")

    def load_from_file(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Список сайтов",
            filetypes=[
                ("Текст и CSV", "*.txt *.csv"),
                ("Текст", "*.txt"),
                ("CSV", "*.csv"),
                ("Все файлы", "*.*"),
            ],
        )
        if not path:
            return
        file_path = Path(path)
        try:
            if file_path.suffix.lower() == ".csv":
                urls = sites_from_csv(file_path)
            else:
                urls = unique_sites(file_path.read_text(encoding="utf-8-sig").splitlines())
        except Exception as error:
            self.log(f"Не удалось прочитать файл: {error}")
            return
        if not urls:
            self.log("В файле не нашлось адресов сайтов.")
            return
        self._table = None
        self.btn_save.configure(text="Сохранить в CSV")
        self._fill_textbox(urls)
        self._refresh_progress_from_state()
        self._sync_buttons()
        self.log(f"Загружено из файла: {len(urls)} — {file_path.name}")

    def load_sites_without_email(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Таблица контактов",
            filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        file_path = Path(path)
        try:
            table = load_contact_table(file_path)
        except Exception as error:
            self.log(f"Не удалось прочитать таблицу: {error}")
            return
        self._apply_table(table)
        pending, ready, without_site, social = summarize_contact_table(table, deep=self._deep)
        self._log_table_queue(file_path.name, pending, ready, without_site, social)

    def clear_list(self):
        if self.is_running:
            return
        self.textbox.delete("1.0", "end")
        with self._state_lock:
            self.results_data = []
            self._done_norms = set()
            self._session_urls = []
            self._table = None
        self.btn_save.configure(text="Сохранить в CSV")
        self._set_progress(0, 0)
        self._sync_buttons()
        self._sync_email_panel()
        self._delete_session()

    def _apply_table(self, table, reset_progress=True):
        self._table = table
        pending = summarize_contact_table(table, deep=self._deep)[0]
        self._fill_textbox(pending)
        self.btn_save.configure(text="Сохранить таблицу")
        if reset_progress:
            with self._state_lock:
                self.results_data = []
                self._done_norms = set()
                self._session_urls = list(pending)
                self._write_session()
        self._refresh_progress_from_state()
        self._sync_buttons()
        self._sync_email_panel()

    def _fill_table_contacts(self, url, email_text, phone_text) -> int:
        if not self._table:
            return 0
        emails = extract_emails([{"emails": email_text}])
        phones = []
        seen_phones = set()
        for part in split_values(phone_text):
            formatted = format_phone(part)
            key = phone_digits(formatted or "")
            if not formatted or key in seen_phones:
                continue
            seen_phones.add(key)
            phones.append(formatted)
        if not emails and not phones:
            return 0
        site_key = normalize(url)
        filled = 0
        for row in self._table["rows"]:
            if site_key not in {normalize(site) for site in row_sites(row)}:
                continue
            changed = False
            if emails:
                changed = merge_slots(row, EMAIL_SLOTS, emails, str.lower) or changed
            if phones:
                changed = merge_slots(row, PHONE_SLOTS, phones, phone_digits) or changed
            if changed:
                filled += 1
        return filled

    def _write_table_file(self):
        table = self._table
        if not table:
            return
        try:
            write_contact_table(table["path"], list(table["fieldnames"]), list(table["rows"]))
        except Exception as error:
            self.log(f"Не удалось записать таблицу: {error}")

    def _pending_urls(self, urls=None):
        source = self._urls_from_box() if urls is None else urls
        return [url for url in source if normalize(url) not in self._done_norms]

    def _refresh_progress_from_state(self):
        urls = self._urls_from_box()
        done = sum(1 for url in urls if normalize(url) in self._done_norms)
        self._set_progress(done, len(urls))

    def _sync_buttons(self):
        busy = self.is_running
        idle = "disabled" if busy else "normal"
        self.btn_start.configure(state=idle)
        self.btn_paste.configure(state=idle)
        self.btn_load.configure(state=idle)
        self.btn_load_missing.configure(state=idle)
        self.btn_clear.configure(state=idle)
        self.slider.configure(state=idle)
        self.deep_check.configure(state=idle)
        pending = self._pending_urls()
        can_resume = (not busy) and bool(pending) and (bool(self.results_data) or bool(self._session_urls))
        self.btn_resume.configure(state="normal" if can_resume else "disabled")
        self.btn_stop.configure(state="normal" if busy else "disabled")

    def stop_scraping(self):
        if not self.is_running:
            return
        self.is_running = False
        self.log("Остановка. Закрываю открытые вкладки, остальные адреса останутся в очереди.")
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_running)

    def _cancel_running(self):
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        if self._browser is not None:
            asyncio.create_task(self._force_close_browser())

    async def _force_close_browser(self):
        browser = self._browser
        self._browser = None
        if browser is None:
            return
        try:
            await asyncio.wait_for(browser.close(), timeout=8)
        except Exception:
            pass

    def start_fresh(self):
        if self.is_running:
            return
        urls = self._urls_from_box()
        if not urls:
            self.log("Список пуст. Вставьте адреса или загрузите файл.")
            return
        with self._state_lock:
            self.results_data = []
            self._done_norms = set()
            self._session_urls = list(urls)
            self._write_session()
        self._set_progress(0, len(urls))
        self._sync_email_panel()
        mode = "глубокий анализ" if self._deep else "главная страница"
        self.log(f"Старт заново: {len(urls)} сайтов, потоков: {self.workers()}, режим: {mode}.")
        self._launch(urls)

    def resume(self):
        if self.is_running:
            return
        urls = self._urls_from_box()
        pending = self._pending_urls(urls)
        if not pending:
            self.log("Продолжать нечего: все адреса уже обработаны.")
            self._sync_buttons()
            return
        with self._state_lock:
            self._session_urls = list(urls)
        done = len(urls) - len(pending)
        self._set_progress(done, len(urls))
        self.log(f"Продолжаю с места остановки. Готово {done}, в очереди {len(pending)}.")
        self._launch(pending)

    def _on_deep_toggle(self):
        self._deep = bool(self.deep_var.get())
        if self._restoring:
            return
        if self._table and not self.is_running:
            pending, ready, without_site, social = summarize_contact_table(self._table, deep=self._deep)
            self._fill_textbox(pending)
            with self._state_lock:
                self._session_urls = list(pending)
                self._write_session()
            self._refresh_progress_from_state()
            self._sync_buttons()
            self._log_table_queue("", pending, ready, without_site, social)
            return
        if self._deep:
            self.log("Глубокий анализ включен: кроме главной открою страницу контактов.")
        else:
            self.log("Обычный режим: открываю главную страницу.")

    def _log_table_queue(self, name, pending, ready, without_site, social):
        title = f"Таблица {name}: " if name else ""
        if not pending:
            if self._deep:
                self.log(
                    f"{title}искать нечего: у строк с обычным сайтом заполнены все телефоны и почты. "
                    f"Полностью заполнены: {ready}, соцсетей: {social}, без сайта: {without_site}."
                )
            else:
                self.log(
                    f"{title}искать нечего: почта и телефон уже есть или нет обычных сайтов. "
                    f"Готовых строк: {ready}, соцсетей: {social}, без сайта: {without_site}."
                )
            return
        if self._deep:
            self.log(
                f"{title}глубокий анализ, в очереди {len(pending)}. "
                f"Полностью заполнены: {ready}, соцсетей: {social}, без сайта: {without_site}. "
                f"Открою главную и страницу контактов. "
                f"«Продолжить» берёт ещё не открытые адреса. "
                f"«Начать» проверит очередь заново и допишет пустые ячейки. "
                f"Проверяю Website и Website2."
            )
            return
        self.log(
            f"{title}в очереди {len(pending)}. "
            f"Уже с почтой и телефоном: {ready}, соцсетей: {social}, без сайта: {without_site}. "
            f"Найденные контакты дописываются в этот же файл. Проверяю Website и Website2."
        )

    def _launch(self, pending):
        self.is_running = True
        self._sync_buttons()
        workers = self.workers()
        deep = self._deep
        thread = threading.Thread(
            target=lambda: asyncio.run(self.scrape_logic(pending, workers, deep)),
            daemon=True,
        )
        thread.start()

    async def process_url(self, url, context, semaphore, deep):
        page = None
        try:
            async with semaphore:
                if not self.is_running:
                    return

                original = url.strip()
                if any(host in original.lower() for host in SOCIAL_HOSTS):
                    self.log(f"Соцсеть, пропуск: {original}")
                    await self._remember(original, "Соцсеть", "")
                    return

                target = original if original.lower().startswith("http") else "http://" + original
                self.log(f"Проверяю: {target}")
                try:
                    page = await context.new_page()
                    emails, phones, finished = await self._collect_site(page, target, deep)
                except asyncio.CancelledError:
                    return
                except Exception as error:
                    if not self.is_running:
                        return
                    message = str(error).split("\n")[0]
                    self.log(f"Ошибка {target}: {message}")
                    await self._remember(original, message, "")
                    return
                finally:
                    if page is not None:
                        try:
                            await page.close()
                        except BaseException:
                            pass
                if not finished or not self.is_running:
                    return
                email_text = ", ".join(emails)
                phone_text = ", ".join(phones)
                if emails or phones:
                    parts = []
                    if emails:
                        parts.append("почта: " + email_text)
                    if phones:
                        parts.append("тел: " + phone_text)
                    self.log(f"Найдено ({target}): {'; '.join(parts)}")
                else:
                    email_text = "Контакты не найдены"
                    self.log(f"Нет контактов: {target}")
                await self._remember(original, email_text, phone_text)
        except asyncio.CancelledError:
            return

    async def _open_and_read(self, page, url, timeout):
        await page.goto(url, timeout=timeout, wait_until="load")
        if not self.is_running:
            return [], [], [], False
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        await asyncio.sleep(1.2)
        if not self.is_running:
            return [], [], [], False
        content = await page.content()
        emails, phones = page_contacts(content)
        links = []
        try:
            links = await page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]')).slice(0, 400).map(a => ({
                    href: a.href || '',
                    text: ((a.innerText || a.getAttribute('aria-label') || '') + '').trim().slice(0, 160)
                }))"""
            )
        except Exception:
            links = []
        if not isinstance(links, list):
            links = []
        return emails, phones, links, True

    async def _collect_site(self, page, target, deep):
        emails, phones, links, finished = await self._open_and_read(page, target, 45000)
        if not finished:
            return emails, phones, False
        if deep and (len(emails) < 3 or len(phones) < 3):
            for link in contact_links(target, links):
                if not self.is_running or (len(emails) >= 3 and len(phones) >= 3):
                    break
                self.log(f"Страница контактов: {link}")
                try:
                    more_emails, more_phones, _, page_ok = await self._open_and_read(page, link, 30000)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if not self.is_running:
                        return emails, phones, False
                    self.log(f"Ошибка {link}: {str(error).split(chr(10))[0]}")
                    continue
                if not page_ok:
                    return emails, phones, False
                merge_found(emails, phones, more_emails, more_phones)
        if not self.is_running:
            return emails, phones, False
        return emails, phones, True

    async def _remember(self, url, email_text, phone_text=""):
        with self._state_lock:
            key = normalize(url)
            if key in self._done_norms:
                return
            self.results_data.append({"url": url, "emails": email_text, "phones": phone_text})
            self._done_norms.add(key)
            filled = self._fill_table_contacts(url, email_text, phone_text)
            self._write_session()
            if filled:
                self._write_table_file()
            done = sum(1 for item in self._session_urls if normalize(item) in self._done_norms)
            total = len(self._session_urls)
        self._set_progress(done, total)
        self._ui_queue.put(("emails", None))

    async def scrape_logic(self, pending, workers, deep=False):
        self._loop = asyncio.get_running_loop()
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                if not self.is_running:
                    await browser.close()
                    return
                self._browser = browser
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                )
                semaphore = asyncio.Semaphore(workers)
                self._tasks = [
                    asyncio.create_task(self.process_url(url, context, semaphore, deep))
                    for url in pending
                ]
                try:
                    await asyncio.gather(*self._tasks, return_exceptions=True)
                finally:
                    self._tasks = []
                    await self._force_close_browser()
        except Exception as error:
            if self.is_running:
                self.log(f"Ошибка движка: {error}")
        finally:
            self.is_running = False
            self._loop = None
            self._tasks = []
            with self._state_lock:
                done = sum(1 for item in self._session_urls if normalize(item) in self._done_norms)
                left = len(self._session_urls) - done
                has_urls = bool(self._session_urls)
            if left == 0 and has_urls:
                self.log("Все сайты обработаны.")
            elif left:
                self.log(f"Остановлено. Осталось {left}. Сессия сохранена, можно закрыть приложение и продолжить позже.")
            if self._table:
                self.log(f"Таблица сохранена: {self._table['path']}")
            self._save_session()
            self._ui_queue.put(("buttons", None))

    def _write_session(self):
        payload = {
            "urls": list(self._session_urls),
            "results": list(self.results_data),
            "table_path": str(self._table["path"]) if self._table else "",
            "deep": bool(self._deep),
        }
        try:
            SESSION_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as error:
            self.log(f"Не удалось сохранить сессию: {error}")

    def _save_session(self):
        with self._state_lock:
            self._write_session()

    def _delete_session(self):
        try:
            SESSION_PATH.unlink(missing_ok=True)
        except Exception:
            pass

    def _restore_session(self):
        if not SESSION_PATH.exists():
            return
        try:
            payload = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        except Exception as error:
            self.log(f"Сессия повреждена и пропущена: {error}")
            return
        urls = payload.get("urls") or []
        results = payload.get("results") or []
        if not isinstance(urls, list) or not isinstance(results, list):
            return
        self._deep = bool(payload.get("deep"))
        self._restoring = True
        try:
            self.deep_var.set(self._deep)
        finally:
            self._restoring = False
        table_path = str(payload.get("table_path") or "").strip()
        table_loaded = False
        if table_path and Path(table_path).is_file():
            try:
                self._apply_table(load_contact_table(Path(table_path)), reset_progress=False)
                table_loaded = True
            except Exception as error:
                self.log(f"Не удалось открыть таблицу сессии: {error}")
        self.results_data = [
            {
                "url": str(item.get("url", "")),
                "emails": str(item.get("emails", "")),
                "phones": str(item.get("phones", "")),
            }
            for item in results
            if isinstance(item, dict)
        ]
        self._done_norms = {normalize(item["url"]) for item in self.results_data if item["url"]}
        if table_loaded:
            pending = self._urls_from_box()
            self._session_urls = list(pending)
            self._refresh_progress_from_state()
            self._sync_email_panel()
            self._sync_buttons()
            left = len(self._pending_urls(pending))
            if left:
                mode = " Глубокий анализ включен." if self._deep else ""
                self.log(
                    f"Таблица восстановлена: в очереди {left} из {len(pending)}.{mode} "
                    f"Нажмите «Продолжить»."
                )
            elif pending:
                self.log("Таблица восстановлена: сайты без почты уже проверены.")
            return
        self._session_urls = [str(url) for url in urls if str(url).strip()]
        if self._session_urls:
            self._fill_textbox(self._session_urls)
        done = sum(1 for url in self._session_urls if normalize(url) in self._done_norms)
        self.progress.set(0 if not self._session_urls else done / len(self._session_urls))
        percent = 0 if not self._session_urls else int(done / len(self._session_urls) * 100)
        self.progress_label.configure(text=f"{done} / {len(self._session_urls)}   ·   {percent}%")
        if self._session_urls and done < len(self._session_urls):
            self.log(f"Сессия восстановлена: {done} из {len(self._session_urls)}. Нажмите «Продолжить».")
        elif self._session_urls and done:
            self.log(f"Загружена прошлая сессия: {done} из {len(self._session_urls)}.")
        self._sync_email_panel()

    def save_session_now(self):
        urls = self._urls_from_box()
        with self._state_lock:
            if urls:
                self._session_urls = list(urls)
            total = len(self._session_urls)
            done = sum(1 for url in self._session_urls if normalize(url) in self._done_norms)
            if total or self.results_data or self._table:
                self._write_session()
            else:
                total = 0
        if not total and not self.results_data and not self._table:
            self.log("Сохранять нечего: список пуст.")
            return
        self._sync_buttons()
        self.log(f"Сессия сохранена: {done} из {total}. После перезапуска нажмите «Продолжить».")

    def _persist_session(self):
        urls = []
        try:
            if self.winfo_exists():
                urls = self._urls_from_box()
        except Exception:
            urls = []
        with self._state_lock:
            if urls:
                self._session_urls = list(urls)
            if self._session_urls or self.results_data or self._table:
                self._write_session()

    def _save_on_exit(self):
        try:
            self._persist_session()
        except Exception:
            pass

    def _on_close(self):
        self.is_running = False
        self._persist_session()
        self.destroy()

    def save_to_csv(self):
        if self._table:
            path = filedialog.asksaveasfilename(
                parent=self,
                initialfile=self._table["path"].name,
                initialdir=str(self._table["path"].parent),
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv")],
            )
            if not path:
                return
            target = Path(path)
            with self._state_lock:
                fieldnames = list(self._table["fieldnames"])
                rows = [dict(row) for row in self._table["rows"]]
            try:
                write_contact_table(target, fieldnames, rows)
            except Exception as error:
                self.log(f"Не удалось записать таблицу: {error}")
                return
            self._table["path"] = target
            self._save_session()
            self.log(f"Таблица сохранена: {target}")
            return
        if not self.results_data:
            self.log("Сохранять нечего: результатов ещё нет.")
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        with open(path, mode="w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["url", "emails", "phones"], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.results_data)
        self.log(f"Файл сохранён: {path}")


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    app = App()
    app.mainloop()
