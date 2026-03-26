import os
import time
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configuration
HARDCOVER_API_KEY = os.getenv('HARDCOVER_API_KEY')
GOODREADS_USER_ID = os.getenv('GOODREADS_USER_ID')
STORYGRAPH_USERNAME = os.getenv('STORYGRAPH_USERNAME')
STORYGRAPH_PASSWORD = os.getenv('STORYGRAPH_PASSWORD')

# URLs
HARDCOVER_API_URL = 'https://api.hardcover.app/v1'
GOODREADS_URL = 'https://www.goodreads.com'
STORYGRAPH_URL = 'https://app.thestorygraph.com'

def get_hardcover_reading_progress():
    """Fetch currently reading books from Hardcover.app"""
    headers = {'Authorization': f'Bearer {HARDCOVER_API_KEY}'}
    response = requests.get(f'{HARDCOVER_API_URL}/user/books?status=currently-reading', headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error fetching from Hardcover: {response.status_code}")
        return []

def update_goodreads(book_title, progress):
    """Update progress on Goodreads (placeholder - API deprecated)"""
    # Goodreads API is deprecated, this would need web scraping or manual update
    print(f"Updating Goodreads for '{book_title}': {progress}%")
    # Placeholder: implement scraping if needed
    pass

def update_storygraph(book_title, progress):
    """Update progress on Storygraph using Selenium"""
    options = Options()
    options.add_argument('--headless')
    driver = webdriver.Chrome(options=options)

    try:
        driver.get(STORYGRAPH_URL)
        # Login
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, 'email')))
        driver.find_element(By.NAME, 'email').send_keys(STORYGRAPH_USERNAME)
        driver.find_element(By.NAME, 'password').send_keys(STORYGRAPH_PASSWORD)
        driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()

        # Wait for login and navigate to reading list
        WebDriverWait(driver, 10).until(EC.url_contains('dashboard'))
        driver.get(f'{STORYGRAPH_URL}/reading-list')

        # Find the book and update progress
        books = driver.find_elements(By.CLASS_NAME, 'book-item')  # Adjust selector
        for book in books:
            if book_title.lower() in book.text.lower():
                # Click to open book details
                book.click()
                # Update progress (adjust selectors based on actual site)
                progress_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'progress-input')))
                progress_input.clear()
                progress_input.send_keys(str(progress))
                driver.find_element(By.ID, 'update-progress').click()
                break
    finally:
        driver.quit()

def sync_progress():
    """Main sync function"""
    books = get_hardcover_reading_progress()
    for book in books:
        title = book['title']
        progress = book.get('progress_percentage', 0)
        update_goodreads(title, progress)
        update_storygraph(title, progress)

def main():
    while True:
        sync_progress()
        time.sleep(15 * 60)  # 15 minutes

if __name__ == '__main__':
    main()