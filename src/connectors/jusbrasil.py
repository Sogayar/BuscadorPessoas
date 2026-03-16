import requests


def search_jusbrasil(name):

    url = f"https://www.jusbrasil.com.br/busca?q={name}"

    r = requests.get(url)

    if r.status_code != 200:
        return []

    links = []

    for line in r.text.split():

        if "jusbrasil.com.br" in line and "href=" in line:

            link = line.split("href=")[1].split('"')[1]

            if link.startswith("http"):
                links.append(link)

    return links