from collections.abc import Iterable

from django.db.models import Q


def build_tokenized_search_query(query: str, lookups: Iterable[str]) -> Q:
    """Build an AND-of-ORs search query from whitespace-separated terms.

    Each term may match any configured lookup, while every term must match.
    This lets a person's first and last names match different database fields.
    """
    terms = [term for term in (query or "").split() if term]
    combined = Q()
    for term in terms:
        per_term = Q()
        for lookup in lookups:
            per_term |= Q(**{lookup: term})
        combined &= per_term
    return combined
