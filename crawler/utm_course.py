import requests
from bs4 import BeautifulSoup


BASE_URL = "https://utm.calendar.utoronto.ca"


def fetch_course_page(course_code):
    url = f"{BASE_URL}/course/{course_code.lower()}"

    response = requests.get(url, timeout=10)

    response.raise_for_status()

    return response.text


def parse_course_page(html):
    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("h1", class_="page-title")

    if title is None:
        raise ValueError("Course title not found.")

    title_text = title.get_text(" ", strip=True)

    parts = title_text.split("•", 1)

    course_code = parts[0].strip()
    course_name = parts[1].strip()

    prerequisites = get_labeled_field(soup, "Prerequisites")
    exclusions = get_labeled_field(soup, "Exclusions")

    return {
        "code": course_code,
        "name": course_name,
        "prerequisites": prerequisites,
        "exclusions": exclusions
    }

def get_labeled_field(soup, field_name):
    labels = soup.find_all("label", class_="field__label")

    for label in labels:
        label_text = label.get_text(" ", strip=True)

        if label_text == field_name:
            container = label.parent

            value = container.find(class_="field__item")

            if value is None:
                return None

            return value.get_text(" ", strip=True)

    return None