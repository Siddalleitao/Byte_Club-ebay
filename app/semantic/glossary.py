"""Bilingual glossary for the semantic layer.

The glossary is deterministic normalisation (rules decide, Rule a), so it
lives in the domain tier; it is re-exported here for the semantic layer.
"""

from app.domain.glossary import GLOSSARY_PATH, Glossary, TermMatch, Vocabulary, get_glossary

__all__ = ["GLOSSARY_PATH", "Glossary", "TermMatch", "Vocabulary", "get_glossary"]
