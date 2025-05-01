import scrapy
import time
from urllib.parse import urljoin
import json
import selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import os
import re
from bs4 import BeautifulSoup


class AlkotekaSpider(scrapy.Spider):
    name = "alkotekasync"

    START_URLS = [
        "https://alkoteka.com/catalog/slaboalkogolnye-napitki-2",
        "https://alkoteka.com/catalog/krepkiy-alkogol",
        "https://alkoteka.com/catalog/vino",
        "https://alkoteka.com/catalog/bezalkogolnye-napitki-1/",
        "https://alkoteka.com/catalog/skidki/options-tovary-so-skidkoi_true",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        # chrome_options.add_argument("--no-sandbox")
        # chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
        self.driver = webdriver.Chrome(options=chrome_options)

    def get_all_product_links_with_selenium(self, url):
        self.driver.get(url)
        SCROLL_PAUSE_TIME = 2
        product_links = set()

        # Прокрутка страницы вниз для подгрузки всех товаров
        full_height = self.driver.execute_script("return document.body.scrollHeight")
        scroll_step = int(full_height * 0.3)
        current_scroll = 0

        while current_scroll < full_height:
            current_scroll += scroll_step
            self.driver.execute_script(f"window.scrollTo(0, {current_scroll});")
            time.sleep(SCROLL_PAUSE_TIME)
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height > full_height:
                full_height = new_height

        elements = self.driver.find_elements(By.CSS_SELECTOR, "div.card-product > a")
        for el in elements:
            link = el.get_attribute("href")
            if link:
                product_links.add(link)

        file_path = os.path.join(os.getcwd(), 'urls.txt')
        with open(file_path, 'w', encoding='utf-8') as file:
            for link in sorted(product_links):
                file.write(link + "\n")

        return list(product_links)

    def start_requests(self):
        urls_file = 'urls.txt'
        if os.path.exists(urls_file) and os.path.getsize(urls_file) > 0:
            # Если файл не пустой, читаем его содержимое и используем ссылки из файла
            with open(urls_file, 'r') as file:
                urls = file.readlines()
                urls = [url.strip() for url in urls]
            for url in urls:
                yield scrapy.Request(
                    url=url,
                    callback=self.parse_product,
                    cookies={
                        "alkoteka_locality": json.dumps({
                            "uuid": "4a70f9e0-46ae-11e7-83ff-00155d026416",
                            "name": "Краснодар",
                            "slug": "krasnodar",
                            "longitude": "38.975996",
                            "latitude": "45.040216",
                            "accented": True
                        }),
                        "alkoteka_age_confirm": "true"
                    },
                    meta={"region": "Краснодар"}
                )
        else:
            # Если файл пустой, парсим как обычно
            for url in self.START_URLS:
                product_links = self.get_all_product_links_with_selenium(url)
                for link in product_links:
                    yield scrapy.Request(
                        url=link,
                        callback=self.parse_product,
                        cookies={
                            "alkoteka_locality": json.dumps({
                                "uuid": "4a70f9e0-46ae-11e7-83ff-00155d026416",
                                "name": "Краснодар",
                                "slug": "krasnodar",
                                "longitude": "38.975996",
                                "latitude": "45.040216",
                                "accented": True
                            }),
                            "alkoteka_age_confirm": "true"
                        },
                        meta={"region": "Краснодар"}
                    )

    def parse_product(self, response):
        url = response.url
        timestamp = int(time.time())
        rpc = url.rstrip('/').split('-')[-1]

        try:
            self.driver.get(url)
            WebDriverWait(self.driver, 12).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.product-info'))
            )
        except TimeoutException:
            pass

        soup = BeautifulSoup(self.driver.page_source, 'html.parser')

        name_ru = soup.h1.get_text(strip=True) if soup.h1 else ''
        name_en = soup.select_one('.product-card__header div p')
        name_en = name_en.get_text(strip=True) if name_en else ''
        title = name_ru or name_en or '—'

        specs = {
            c.find('span').get_text(strip=True):
                c.select_one('p').get_text(' ', strip=True)
            for c in soup.select('.specifications-card')
        }
        artikul = specs.get('Артикул') or rpc.split('_')[-1]

        volume = specs.get('Объем') or specs.get('Объём') or ''
        colour = specs.get('Цвет') or ''
        for addon in (volume, colour):
            if addon and addon not in title:
                title = f'{title}, {addon}'

        brand = specs.get('Бренд') or name_en
        if not brand:
            brand = title.split(',')[0].split()[0]

        crumbs = [li.get_text(strip=True) for li in soup.select('ul.breadcrumbs li')]
        if not crumbs:
            slug_section = url.split('/product/', 1)[-1].split('/', 1)[0]
            human = re.sub(r'-?\d+$', '', slug_section).replace('-', ' ').strip().title()
            if human:
                crumbs = [human]
        section = crumbs

        raw_tags = [p.get_text(strip=True) for p in soup.select('.product-card__tags p')]

        # positive = r'(скид|хит|популяр|акц|new|новинк|подар)'
        # marketing_tags = [t for t in raw_tags if re.search(positive, t, re.I)]
        # marketing_tags = list(dict.fromkeys(marketing_tags))

        pb = soup.select_one('.button-count__title')
        if pb:
            old = pb.span.get_text(strip=True) if pb.span else ''
            cur = pb.get_text(strip=True).replace(old, '')
            original = float(re.sub(r'[^\d.]', '', old).replace(',', '.')) if old else None
            current = float(re.sub(r'[^\d.]', '', cur).replace(',', '.')) if cur else None
        else:
            original = current = None

        if not current and original:    current = original
        if not original and current:    original = current

        sale_tag = ''
        if original and current and original > current:
            disc = int(round((original - current) / original * 100))
            sale_tag = f'Скидка {disc}%'

        store_el = soup.select_one('.product-card__interactives-anchor a')
        if store_el:
            count = int(re.sub(r'\D', '', store_el.get_text()))
            in_stock = count > 0
        else:
            count = 0
            in_stock = False

        main_img = soup.select_one('.product-info__hero-img-wrap img')
        main_img = main_img['src'] if main_img else ''
        set_imgs = [img['src'] for img in soup.select('.product-info__hero-img-wrap img')]
        if not set_imgs and main_img:
            set_imgs = [main_img]

        desc_block = soup.find('h3', string=lambda x: x and 'Описание' in x)
        description = ''
        if desc_block:
            description = desc_block.find_next('p', class_='product-info__description-text') \
                .get_text(' ', strip=True)
        if not description:
            meta = soup.find('meta', attrs={'name': 'description'})
            description = meta['content'].strip() if meta and meta.get('content') else ''

        feat_block = soup.find('h3', string=lambda x: x and 'Особенности производства' in x)
        features = ''
        if feat_block:
            features = feat_block.find_next('p', class_='product-info__description-text') \
                .get_text(' ', strip=True)

        matches = [b.p.get_text(strip=True) for b in soup.select('.matches-item p')]

        vol_tags = {re.sub(r'[^\d.,]', '', t) for t in raw_tags if re.search(r'мл|л\b', t, re.I)}
        if volume:
            vol_tags.add(re.sub(r'[^\d.,]', '', volume))
        variants = max(1, len([v for v in vol_tags if v]))

        metadata = {
            '__description': description,
            'Артикул': artikul,
            'Особенности': features,
            'Гастро-сочетания': ', '.join(matches) if matches else ''
        }
        metadata.update(specs)

        yield {
            'timestamp': timestamp,
            'RPC': rpc,
            'url': url,
            'title': title,
            'marketing_tags': raw_tags,
            'brand': brand,
            'section': section,
            'price_data': {
                'current': current,
                'original': original,
                'sale_tag': sale_tag
            },
            'stock': {
                'in_stock': in_stock,
                'count': count
            },
            'assets': {
                'main_image': main_img,
                'set_images': set_imgs,
                'view360': [],
                'video': []
            },
            'metadata': metadata,
            'variants': variants
        }
