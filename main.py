from crawler.utm_course import fetch_course_page, parse_course_page


html = fetch_course_page("CSC148H5")

course = parse_course_page(html)

print(course)