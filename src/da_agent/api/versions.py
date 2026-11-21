"""What the API checks before it accepts a user's decisions on a dataset version.

Pure code over the version's stored check result and the request body. Anything that would make
the build step fail or write what only code may write is refused here, with a readable reason.
"""

from typing import Any

from pydantic import ValidationError

from da_agent.ingestion.spec import DatasetSpec
from da_agent.ingestion.version_review import invalid_choices
from da_agent.ingestion.versioning import RepairChange, apply_repairs


class ApprovalInvalidError(ValueError):
    """The decisions cannot be carried out."""


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()[:5]
    )


def checked_choices(
    entries: list[dict[str, Any]],
    chosen: dict[str, str],
    replace_files: dict[str, str],
    current_files: list[dict[str, Any]],
    apply_anyway: bool,
) -> dict[str, Any]:
    """The choices as stored. Every table with overlaps needs an allowed choice, and a table
    chosen `replace` names a stored file of the current version, which no other choice may."""
    wrong = invalid_choices(entries, chosen)
    if wrong:
        raise ApprovalInvalidError(f"missing or not allowed choice for: {', '.join(wrong)}")
    stored = {f.get("stored_as") for f in current_files}
    replacing = {t for t, c in chosen.items() if c == "replace"}
    if set(replace_files) != replacing:
        raise ApprovalInvalidError(
            "replace_files must name one file for each table chosen replace, and no other"
        )
    unknown = sorted(name for name in replace_files.values() if name not in stored)
    if unknown:
        raise ApprovalInvalidError(f"not files of the current version: {', '.join(unknown)}")
    return {"tables": chosen, "replace_files": replace_files, "apply_anyway": apply_anyway}


def checked_edits(
    entries: list[dict[str, Any]], edits: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Edited specs may only describe tables that are new in this version, and never carry
    `drop_overlap`: only code writes that."""
    if edits is None:
        return None
    try:
        spec = DatasetSpec.model_validate(edits)
    except ValidationError as error:
        raise ApprovalInvalidError(f"the edited spec is invalid: {_problems(error)}") from error
    new = {e["table"] for e in entries if e.get("kind") == "new"}
    existing = sorted(t.name for t in spec.tables if t.name not in new)
    if existing:
        raise ApprovalInvalidError(f"only new tables can be edited, not: {', '.join(existing)}")
    if any(t.drop_overlap for t in spec.tables):
        raise ApprovalInvalidError("drop_overlap is set by the system, not by an edit")
    return spec.model_dump(mode="json")


def checked_repairs(
    accepted: list[dict[str, Any]], proposed: list[dict[str, Any]], spec: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Accepted repairs are a subset of the proposed ones, and the spec with them applied is
    still a valid spec."""
    try:
        wanted = [RepairChange.model_validate(r) for r in accepted]
        offered = [RepairChange.model_validate(r) for r in proposed]
    except ValidationError as error:
        raise ApprovalInvalidError(f"a repair is invalid: {_problems(error)}") from error
    extra = [r.path for r in wanted if r not in offered]
    if extra:
        raise ApprovalInvalidError(f"repairs that were not proposed: {', '.join(extra)}")
    try:
        repaired = apply_repairs(DatasetSpec.model_validate(spec or {"tables": []}), wanted)
        DatasetSpec.model_validate(repaired.model_dump(mode="json"))
    except (ValidationError, ValueError, TypeError) as error:
        raise ApprovalInvalidError(f"the repairs do not give a valid spec: {error}") from error
    return [r.model_dump(mode="json") for r in wanted]
