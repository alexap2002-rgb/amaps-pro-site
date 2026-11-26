"""Проверяет публикацию страниц и ресурсов через локальный HTTP-сервер."""

import sys
from contextlib import closing
from functools import partial
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPException
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree


SITE_ROOT = Path(__file__).resolve().parents[1]
SITE_URL = "https://amaps-pro.ru"
PAGES = ("/", "/zakazchikam.html", "/ispolnitelyam.html", "/izgotovlenie-detalei-po-chertezham.html", "/privacy.html")


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.in_heading = False
        self.has_heading = False
        self.has_form = False
        self.images = {}
        self.feed(body.decode("utf-8"))
        self.close()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "h1":
            self.in_heading = True
        if tag == "form" and attrs.get("id") == "request-form":
            self.has_form = True
        if tag == "link":
            relations = (attrs.get("rel") or "").split()
            if "icon" in relations:
                self.images["favicon"] = attrs.get("href")
            if "preload" in relations and attrs.get("as") == "image":
                self.images["hero"] = attrs.get("href")
        if tag == "meta" and attrs.get("property") == "og:image":
            self.images["og:image"] = attrs.get("content")

    def handle_data(self, data):
        if self.in_heading and data.strip():
            self.has_heading = True

    def handle_endtag(self, tag):
        if tag == "h1":
            self.in_heading = False


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def fetch(connection, path, *expected_types):
    connection.request("GET", path)
    with connection.getresponse() as response:
        if response.status != 200:
            raise ValueError(f"{path}: HTTP {response.status}")
        content_type = response.headers.get_content_type()
        if content_type not in expected_types:
            raise ValueError(f"{path}: получен неожиданный тип {content_type}")
        return response.read()


def check_site(connection):
    pages = {path: Page(fetch(connection, path, "text/html")) for path in PAGES}
    for path, page in pages.items():
        if not page.has_heading:
            raise ValueError(f"{path} должен содержать непустой заголовок h1")
    home = pages["/"]
    if not home.has_form:
        raise ValueError("На главной странице отсутствует форма обращения")

    robots = fetch(connection, "/robots.txt", "text/plain")
    if f"Sitemap: {SITE_URL}/sitemap.xml".encode("utf-8") not in robots:
        raise ValueError("/robots.txt: отсутствует ссылка на карту сайта")

    sitemap = fetch(connection, "/sitemap.xml", "application/xml", "text/xml")
    try:
        xml = ElementTree.fromstring(sitemap)
    except ElementTree.ParseError as error:
        raise ValueError(f"/sitemap.xml: некорректный XML ({error})") from error
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    sitemap_locations = {location.text for location in xml.findall("sm:url/sm:loc", namespace)}
    for path in PAGES:
        if SITE_URL + path not in sitemap_locations:
            raise ValueError(f"/sitemap.xml: отсутствует адрес {SITE_URL + path}")

    image_paths = {}
    for role in ("hero", "favicon", "og:image"):
        link = home.images.get(role)
        if not link:
            raise ValueError(f"На главной странице отсутствует ссылка на изображение {role}")
        address = urlsplit(urljoin(SITE_URL + "/", link))
        if (address.scheme, address.netloc) != ("https", "amaps-pro.ru"):
            raise ValueError(f"Изображение {role} должно находиться на сайте АМАПС")
        image_paths[role] = address.path

    hero_path = image_paths["hero"]
    hero = fetch(connection, hero_path, "image/webp")
    if hero[:4] != b"RIFF" or hero[8:12] != b"WEBP":
        raise ValueError(f"{hero_path}: отсутствует заголовок WebP")

    for path in (image_paths["favicon"], image_paths["og:image"]):
        image = fetch(connection, path, "image/png")
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"{path}: отсутствует сигнатура PNG")


def main():
    handler = partial(QuietHandler, directory=str(SITE_ROOT))
    try:
        with HTTPServer(("127.0.0.1", 0), handler) as server:
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                with closing(HTTPConnection(*server.server_address, timeout=5)) as connection:
                    check_site(connection)
            finally:
                server.shutdown()
                worker.join()
    except (HTTPException, OSError, ValueError) as error:
        print(f"Ошибка локальной проверки сайта: {error}", file=sys.stderr)
        return 1

    print("Локальная проверка сайта пройдена: 5 страниц, robots.txt, sitemap.xml и 3 изображения.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
