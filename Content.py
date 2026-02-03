import csv
import re
import time
import signal
import sys
import os
import argparse
import urllib.parse
from playwright.sync_api import sync_playwright

FILTER_LABELS = [
    "Distance",
    "Job Type",
    "Minimum Salary",
    "Date added",
]


jobs = []
def get_unique_output_path(base_path):
    if not os.path.exists(base_path):
        return base_path

    name, ext = os.path.splitext(base_path)
    counter = 2

    while True:
        new_path = f"{name}_{counter}{ext}"
        if not os.path.exists(new_path):
            return new_path
        counter += 1


# ---------- CLI ----------
parser = argparse.ArgumentParser()
parser.add_argument("--job", required=True, help="Job title to search")
parser.add_argument("--pages", type=int, default=5)
parser.add_argument("--location", help="Job location (optional)")
args = parser.parse_args()


JOB_TITLE = args.job
MAX_PAGES = args.pages
LOCATION = args.location


# ---------- URL ----------
ENCODED_TITLE = urllib.parse.quote(JOB_TITLE)
if LOCATION:
    ENCODED_LOCATION = urllib.parse.quote(LOCATION)
    BASE_URL = f"https://www.simplyhired.com/search?q={ENCODED_TITLE}&l={ENCODED_LOCATION}"
else:
    BASE_URL = f"https://www.simplyhired.com/search?q={ENCODED_TITLE}"


# ---------- Output ----------
safe_title = JOB_TITLE.lower().replace(" ", "_")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_OUTPUT = os.path.join(BASE_DIR, f"{safe_title}_jobs.csv")
OUTPUT_FILE = get_unique_output_path(BASE_OUTPUT)


def handle_exit(signum, frame):
    print("\n[!] stop crawling, exporting CSV...")
    if jobs:
        export_csv(jobs)
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)


def wait(ms):
    time.sleep(ms / 1000)


def check_404_page(page):
    try:
        error_h1 = page.locator("[data-testid='404PageH1']")
        if error_h1.is_visible(timeout=3000):
            print("\n❌ SimplyHired returned a 404 error.")
            print("⚠️ The website has an internal issue or the search parameters are invalid.")
            print("👉 Please try again later or adjust job title / location / filters.\n")
            return True
    except:
        pass
    return False


def check_no_results(page, job_title, location=None):
    try:
        headings = page.get_by_role("heading", level=2)

        count = headings.count()
        if count == 0:
            return False

        for i in range(count):
            text = headings.nth(i).inner_text().strip().lower()

            if "could not find any" in text:
                print("\n⚠️ No jobs found.")

                if location:
                    print(
                        f"👉 We could not find any {job_title} jobs in {location}."
                    )
                else:
                    print(
                        f"👉 We could not find any {job_title} jobs."
                    )

                print("💡 Please adjust your job title, location, or filters.\n")
                return True

    except Exception as e:
        print(f"[WARN] Error checking for no results: {e}")
        pass

    return False


def normalize_filters_for_csv(selected_filters):
    return {
        "distance": selected_filters.get("Distance", ""),
        "job_type": selected_filters.get("Job Type", ""),
        "date_added": selected_filters.get("Date added", ""),
    }


def ask_filter_choice_dynamic(filter_name, options):
    if not options:
        print(f"\n⚠️ No available options for {filter_name}, skipping")
        return None

    print(f"\n🔹 {filter_name}")
    use = input(f"Do you want to apply this filter? (y/n): ").strip().lower()

    if use != "y":
        return None
    
    print("\nAvailable options:")
    for i, opt in enumerate(options, 1):
        print(f"{i}. {opt}")

    while True:
        choice = input("Enter the number of your choice: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        else:
            print("Invalid choice. Please try again.")


def get_dropdown_options(page, label_text):
    try:
        label = page.locator(f"text={label_text}").first
        label.wait_for(state="visible", timeout=10000)
        label.scroll_into_view_if_needed()

        dropdown_button = label.locator(
            "xpath=following::button[@data-testid='dropdown']"
        ).first
        dropdown_button.click()

        dropdown_menu = page.locator(
            "[data-testid='dropdown-list']:visible"
        ).first
        dropdown_menu.wait_for(state="visible", timeout=5000)

        options = dropdown_menu.locator("[data-testid='dropdown-option']")
        texts = []

        for i in range(options.count()):
            text = options.nth(i).inner_text().strip()
            if text:
                texts.append(text)

        page.keyboard.press("Escape")
        time.sleep(0.3)

        return texts

    except Exception as e:
        print(f"[WARN] Could not read options for '{label_text}': {e}")
        try:
            page.keyboard.press("Escape")
        except:
            pass
        return []


def select_filter_option(page, label_text, option_text):
    try:
        label = page.locator(f"text={label_text}").first
        label.scroll_into_view_if_needed()

        dropdown_button = label.locator(
            "xpath=following::button[@data-testid='dropdown']"
        ).first
        dropdown_button.click()

        dropdown_menu = page.locator(
            "[data-testid='dropdown-list']"
        ).filter(has=page.locator(":visible"))

        dropdown_menu.wait_for(state="visible", timeout=5000)

        options = dropdown_menu.locator("[data-testid='dropdown-option']")
        count = options.count()

        for i in range(count):
            opt = options.nth(i)
            if option_text.lower() in opt.inner_text().lower():
                opt.scroll_into_view_if_needed()
                opt.click(force=True)
                print(f"[INFO] Filter applied: {label_text} → {option_text}")
                time.sleep(1)
                return True

        # ❌ Option not found
        print(f"[WARN] Option '{option_text}' not found for filter '{label_text}', skipping")

        # close dropdown cleanly
        page.keyboard.press("Escape")
        time.sleep(0.5)
        return False

    except Exception as e:
        print(f"[WARN] Failed to apply filter '{label_text}': {e}")
        page.keyboard.press("Escape")
        return False


def go_to_page(page, page_number):
    selector = f'[data-testid="paginationBlock{page_number}"]'

    try:
        page.wait_for_selector(selector, timeout=10000)
        el = page.query_selector(selector)
        if not el:
            print(f"[INFO] Page {page_number} not available yet")
            return False

        # scroll JUST enough to bring it into view
        el.scroll_into_view_if_needed()
        time.sleep(0.5)
        el.click()
        page.wait_for_load_state("networkidle")
        return True

    except Exception as e:
        print(f"[WARN] Could not navigate to page {page_number}: {e}")
        return False


# ---------- Crawl ----------
def crawl():
    global jobs
    fingerprints = set()
    current_page = 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(BASE_URL)

        try:
            consent_btn = page.query_selector('button:has-text("Accept")')
            if consent_btn:
                consent_btn.click()
                wait(2000)
        except:
            pass

        page.wait_for_load_state("networkidle")

        if check_404_page(page):
            browser.close()
            return
        
        if check_no_results(page, JOB_TITLE, LOCATION):
            browser.close()
            return
        
        time.sleep(2)
        
        selected_filters = {}

        # Dynamically read options
        for label in FILTER_LABELS:
            options = get_dropdown_options(page, label)

            if not options:
                print(f"[INFO] No options available for {label}, skipping")
                continue

            choice = ask_filter_choice_dynamic(label, options)

            if choice:
                selected_filters[label] = choice
        
        for lable, option in selected_filters.items():
            select_filter_option(page, lable, option)
            page.wait_for_load_state("networkidle")
            wait(2000)
            time.sleep(1)

            if check_404_page(page):
                browser.close()
                return
            
            if check_no_results(page, JOB_TITLE, LOCATION):
                browser.close()
                return

        while current_page <= MAX_PAGES:
            print(f"[+] Crawling page {current_page}")

            if check_404_page(page):
                browser.close()
                return
            
            if check_no_results(page, JOB_TITLE, LOCATION):
                browser.close()
                return
            
            page.wait_for_selector(
                '[data-testid="searchSerpJob"]', 
                state="attached", 
                timeout=30000)
            
            cards = page.query_selector_all('[data-testid="searchSerpJob"]')
            print(f"[DEBUG] Found {len(cards)} job cards")

            for card in cards:
                title_el = card.query_selector("a")
                if not title_el:
                    continue    

                title = title_el.inner_text().strip() if title_el else "N/A"
                link = title_el.get_attribute("href") if title_el else None
                link = f"https://www.simplyhired.com{link}" if link else "N/A"


                company = (
                    card.query_selector("span.jobposting-company")
                    or card.query_selector('[data-testid="companyName"]')
                )
                company = company.inner_text().strip() if company else "N/A"

                location = (
                    card.query_selector("span.jobposting-location")
                    or card.query_selector('[data-testid="searchSerpJobLocation"]')
                )
                location = location.inner_text().strip() if location else "N/A"

                salary = (
                    card.query_selector("span.SalaryEstimate")
                    or card.query_selector("span:has-text('$')")
                )
                salary = salary.inner_text().strip() if salary else "N/A"

                fingerprint = f"{company}{title}{location}".lower().replace(" ", "")
                if fingerprint in fingerprints:
                    continue
                
                filter_meta = normalize_filters_for_csv(selected_filters)

                job = {
                    "search": JOB_TITLE,
                    "company": company,
                    "title": title,
                    "link": link,
                    "salary": salary,
                    "location": location,
                    "distance": filter_meta["distance"],
                    "job_type": filter_meta["job_type"],
                    "date_added": filter_meta["date_added"],
                    "page": current_page
                }

                jobs.append(job)
                print("[DEBUG] Adding job:", title)
                print("[DEBUG] Job appended")

                fingerprints.add(fingerprint)
                print("  -", title)

            next_page = current_page + 1
            if next_page > MAX_PAGES:
                break

            print(f"[INFO] Clicking page {next_page}")
            success = go_to_page(page, next_page)

            if not success:
                print("[INFO] No more pages found.")
                break

            current_page += 1
            time.sleep(2)

        print(f"[INFO] Finished crawling {current_page} page(s)")
        browser.close()

    return jobs


# ---------- CSV ----------
def export_csv(jobs):
    print("[DEBUG] Exporting CSV...")
    print(f"[DEBUG] OUTPUT_FILE: {OUTPUT_FILE}")
    print(f"[DEBUG] jobs length before export: {len(jobs)}")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Search Keyword", 
            "Search Location",
            "Company", 
            "Job Title", 
            "Link", 
            "Salary", 
            "Location", 
            "distance",
            "job_type",
            "date_added",
            "Page"
        ])

        for j in jobs:
            writer.writerow([
                JOB_TITLE,
                LOCATION if LOCATION else "Any",
                j["company"],
                j["title"],
                j["link"],
                j["salary"],
                j["location"],
                j["distance"],
                j["job_type"],
                j["date_added"],
                j["page"]
            ])

    print(f"\n✅ Exported {len(jobs)} jobs to {OUTPUT_FILE}")


# ---------- Run ----------
try: 
    crawl()
finally:
    print("[DEBUG] jobs length:", len(jobs))
    export_csv(jobs)