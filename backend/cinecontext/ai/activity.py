def shorten(value: str, limit: int = 180) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def joined_titles(films, limit: int = 5) -> str:
    titles = [film.title for film in films[:limit]]
    if len(films) > limit:
        titles.append(f"and {len(films) - limit} more")
    return ", ".join(titles)
