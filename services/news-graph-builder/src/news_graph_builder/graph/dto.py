from typing import NamedTuple


class Entity(NamedTuple):
    name: str
    type: str


class Relation(NamedTuple):
    source: str
    target: str
    type: str
    description: str


class Extraction(NamedTuple):
    title: str
    summary: str
    entities: list[Entity]
    relations: list[Relation]
