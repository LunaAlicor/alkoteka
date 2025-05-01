import scrapy
import json
import re
import time
import os
from bs4 import BeautifulSoup


class AlkotekaSpider(scrapy.Spider):

    name = "alkoteka_async"
    PROXY = "http://79.110.201.235:8081"

    START_URLS = [
        "https://alkoteka.com/catalog/slaboalkogolnye-napitki-2",
        "https://alkoteka.com/catalog/krepkiy-alkogol",
        "https://alkoteka.com/catalog/vino",
        "https://alkoteka.com/catalog/bezalkogolnye-napitki-1/",
        "https://alkoteka.com/catalog/skidki/options-tovary-so-skidkoi_true",
    ]

    custom_settings = {
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 60000,
        "CONCURRENT_REQUESTS": 2,
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": 2,
        "AUTOTHROTTLE_ENABLED": True,
    }

    COOKIES = {
        "alkoteka_locality": json.dumps({
            "uuid": "4a70f9e0-46ae-11e7-83ff-00155d026416",
            "name": "Краснодар",
            "slug": "krasnodar",
            "longitude": "38.975996",
            "latitude": "45.040216",
            "accented": True,
        }),
        "alkoteka_age_confirm": "true",
    }

    def start_requests(self):
        urls_file = "urls.txt"

        if os.path.exists(urls_file) and os.path.getsize(urls_file) > 0:
            with open(urls_file, encoding="utf-8") as f:
                for line in f:
                    url = line.strip()
                    if not url:
                        continue
                    yield scrapy.Request(
                        url,
                        cookies=self.COOKIES,
                        meta={"playwright": True, "playwright_include_page": True,
                              "region": "Краснодар",
                              "proxy": self.PROXY,
                              },
                        callback=self.parse_product,
                    )
        else:
            for url in self.START_URLS:
                yield scrapy.Request(
                    url,
                    cookies=self.COOKIES,
                    meta={"playwright": True, "playwright_include_page": True,
                          "region": "Краснодар",
                          "proxy": self.PROXY,
                          },
                    callback=self.parse_listing,
                )

    async def parse_listing(self, response):
        page = response.meta["playwright_page"]
        await page.wait_for_load_state("networkidle")

        previous_height = None
        while True:
            current_height = await page.evaluate("document.body.scrollHeight")
            if previous_height == current_height:
                break
            previous_height = current_height
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(15000)

        links = await page.eval_on_selector_all(
            "div.card-product > a", "elements => elements.map(el => el.href)"
        )

        await page.close()

        for link in links:
            yield scrapy.Request(
                link,
                cookies=self.COOKIES,
                meta={"playwright": True, "playwright_include_page": True,
                      "region": "Краснодар",
                      "proxy": self.PROXY,
                      },
                callback=self.parse_product,
            )

    async def parse_product(self, response):

        page = response.meta["playwright_page"]
        try:
            await page.wait_for_selector(".product-info", timeout=30_000)
        except Exception:
            self.logger.debug("Timeout waiting for product-info on %s", response.url)

        html = await page.content()
        await page.close()
        soup = BeautifulSoup(html, "html.parser")

        url = response.url
        timestamp = int(time.time())
        rpc = url.rstrip("/").split("-")[-1]

        name_ru = soup.h1.get_text(strip=True) if soup.h1 else ""
        name_en_tag = soup.select_one(".product-card__header div p")
        name_en = name_en_tag.get_text(strip=True) if name_en_tag else ""
        title = name_ru or name_en or "—"     #TODO мб подождать для прогрузки или фикс

        specs = {
            c.find("span").get_text(strip=True): c.select_one("p").get_text(" ", strip=True)
            for c in soup.select(".specifications-card")
        }
        artikul = specs.get("Артикул") or rpc.split("_")[-1]

        volume = specs.get("Объем") or specs.get("Объём") or ""
        colour = specs.get("Цвет") or ""
        for addon in (volume, colour):
            if addon and addon not in title:
                title = f"{title}, {addon}"

        brand = specs.get("Бренд") or name_en
        if not brand:
            brand = title.split(",")[0].split()[0]

        crumbs = [li.get_text(strip=True) for li in soup.select("ul.breadcrumbs li")]
        if not crumbs:
            slug_section = url.split("/product/", 1)[-1].split("/", 1)[0]
            human = re.sub(r"-?\d+$", "", slug_section).replace("-", " ").strip().title()
            if human:
                crumbs = [human]
        section = crumbs

        raw_tags = [p.get_text(strip=True)
                    for p in soup.select(".product-card__tags p")]
        all_tags = list(dict.fromkeys(raw_tags))

        pb = soup.select_one(".button-count__title")
        if pb:
            old = pb.span.get_text(strip=True) if pb.span else ""
            cur = pb.get_text(strip=True).replace(old, "")
            original = float(re.sub(r"[^\d.]", "", old).replace(",", ".")) if old else None
            current = float(re.sub(r"[^\d.]", "", cur).replace(",", ".")) if cur else None
        else:
            original = current = None

        if not current and original:
            current = original
        if not original and current:
            original = current

        sale_tag = ""
        if original and current and original > current:
            disc = int(round((original - current) / original * 100))
            sale_tag = f"Скидка {disc}%"

        store_el = soup.select_one(".product-card__interactives-anchor a")
        if store_el:
            count = int(re.sub(r"\D", "", store_el.get_text()))
            in_stock = count > 0
        else:
            count = 0
            in_stock = False

        main_img_tag = soup.select_one(".product-info__hero-img-wrap img")
        main_img = main_img_tag["src"] if main_img_tag else ""
        set_imgs = [img["src"] for img in soup.select(".product-info__hero-img-wrap img")]
        if not set_imgs and main_img:
            set_imgs = [main_img]

        desc_block = soup.find("h3", string=lambda x: x and "Описание" in x)
        description = ""
        if desc_block:
            description = desc_block.find_next("p", class_="product-info__description-text").get_text(" ", strip=True)
        if not description:
            meta = soup.find("meta", attrs={"name": "description"})
            description = meta["content"].strip() if meta and meta.get("content") else ""

        feat_block = soup.find("h3", string=lambda x: x and "Особенности производства" in x)
        features = ""
        if feat_block:
            features = feat_block.find_next("p", class_="product-info__description-text").get_text(" ", strip=True)

        matches = [b.p.get_text(strip=True) for b in soup.select(".matches-item p")]

        vol_tags = {re.sub(r"[^\d.,]", "", t) for t in raw_tags if re.search(r"мл|л\b", t, re.I)}
        if volume:
            vol_tags.add(re.sub(r"[^\d.,]", "", volume))
        variants = max(1, len([v for v in vol_tags if v]))

        metadata = {
            "__description": description,
            "Артикул": artikul,
            "Особенности": features,
            "Гастро-сочетания": ", ".join(matches) if matches else "",
        }
        metadata.update(specs)

        yield {
            "timestamp": timestamp,
            "RPC": rpc,
            "url": url,
            "title": title,
            "marketing_tags": all_tags,
            "brand": brand,
            "section": section,
            "price_data": {
                "current": current,
                "original": original,
                "sale_tag": sale_tag,
            },
            "stock": {"in_stock": in_stock, "count": count},
            "assets": {
                "main_image": main_img,
                "set_images": set_imgs,
                "view360": [],
                "video": [],
            },
            "metadata": metadata,
            "variants": variants,
        }
