"""Read and edit a Publii CMS site from code, safely enough for an AI agent.

Layers, lowest first:

    models      pydantic schemas for the SQLite rows and config JSON
    repository  read access, plus PubliiTransaction for atomic writes
    sync        compare two sites and propagate differences
    agent       a JSON command interface with plan and apply

Import what matches the job. Most callers want PubliiRepository, or
AgentSession if a model is driving.
"""

from .agent import AgentSession, Command, CommandError, describe_operations
from .models import (
    CoreMeta,
    IntegrityIssue,
    Menu,
    MenuItem,
    Post,
    PostAdditionalData,
    SitePaths,
    Tag,
)
from .repository import (
    PubliiIntegrityError,
    PubliiLockedError,
    PubliiRepository,
    PubliiTransaction,
)

__version__ = "1.0.0"

__all__ = [
    "AgentSession",
    "Command",
    "CommandError",
    "CoreMeta",
    "IntegrityIssue",
    "Menu",
    "MenuItem",
    "Post",
    "PostAdditionalData",
    "PubliiIntegrityError",
    "PubliiLockedError",
    "PubliiRepository",
    "PubliiTransaction",
    "SitePaths",
    "Tag",
    "describe_operations",
    "__version__",
]
