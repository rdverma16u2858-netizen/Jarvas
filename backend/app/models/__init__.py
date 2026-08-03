"""
ORM models.

Every model must be imported HERE. Alembic's autogenerate walks
`Base.metadata`, and a model class that is never imported is never registered
on it — so the table silently does not appear in the migration. That failure
mode is quiet and only surfaces in production when a query hits a missing
table, which is why the imports are centralised rather than left to whoever
happens to import what.
"""

from app.models.conversation import Conversation, Turn
from app.models.question import PracticeQuestion
from app.models.quiz import Quiz, QuizAnswer
from app.models.review import ReviewRecord

__all__ = [
    "Conversation",
    "PracticeQuestion",
    "Quiz",
    "QuizAnswer",
    "ReviewRecord",
    "Turn",
]
