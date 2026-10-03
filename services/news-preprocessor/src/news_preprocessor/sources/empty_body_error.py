class EmptyBodyError(Exception):
    pass


class ImageOnlyArticleError(EmptyBodyError):
    def __init__(self, image_count: int) -> None:
        self.image_count = image_count
