"""V001 required fields present; V010 values parsed; V012 fields required from a date (field dictionary)."""

from __future__ import annotations

import pandas as pd

from rules.base import Rule, RuleContext, Severity, Violations


class V001RequiredFields(Rule):
    id = "V001"
    name = "required fields present"
    severity = "block"
    message = "Required field(s) blank: {missing}"
    suggested_fix = "Ask the plant to resend the line with the missing field(s) filled"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        required = ctx.fields.always  # metrics/fields/: the fields every row needs, whatever its date
        blank = df[required].isna()
        for c in required:  # a value that failed to parse is V010's, not a blank
            blank[c] &= ~df["parse_errors"].map(lambda errors, col=c: col in errors)
        hit = blank[blank.any(axis=1)]
        return Violations(list(hit.index), [{"missing": [c for c in required if row[c]]}
                                            for _, row in hit.iterrows()])


class V010ValuesParsed(Rule):
    id = "V010"
    name = "values parse with the source's detected formats"
    severity = "block"
    message = "Value(s) did not parse: {unparsed}"
    suggested_fix = "Correct the value at source, or confirm a mapping with the right parser for this header"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        bad = df[df["parse_errors"].map(bool)]
        return Violations(list(bad.index), [{"unparsed": errors} for errors in bad["parse_errors"]])


class V012RequiredFromDate(Rule):
    """A field the dictionary requires from a date (plan A11, the month-15 change request). Rows dated on or
    after it that lack the field are blocked; rows dated before it are queued as "not covered" warnings, so
    history is neither dropped nor blocked retroactively. A column the batch's mapping does not supply counts
    as blank: the extract did not carry it. Rows with no date to compare are V001's (or V010's)."""

    id = "V012"
    name = "field required by the field dictionary from its effective date"
    severity = "block"
    message = "{reason}"
    suggested_fix = "{fix}"

    def owner(self, ctx: RuleContext) -> str:
        return ctx.fields.owner

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        found: dict[int, tuple[Severity, str, dict]] = {}
        for req in ctx.fields.dated:
            if req.required_from is None or req.by is None:  # the dictionary guarantees both on .dated
                continue
            if req.column in ctx.mapped:
                missing = df[req.column].isna() & ~df["parse_errors"].map(lambda e, c=req.column: c in e)
            else:
                missing = pd.Series(True, index=df.index)
            dated = df[req.by]
            start = pd.Timestamp(req.required_from)
            for i in df.index[missing & dated.notna()]:
                if i in found and found[i][0] == "block":
                    continue
                blocked = dated[i] >= start
                how = "blank" if req.column in ctx.mapped else "not in this source's extract"
                reason = (f"{req.column} required from {req.required_from} (field dictionary "
                          f"v{ctx.fields.version}) is {how}" if blocked else
                          f"{req.column} not covered before {req.required_from}: {how}")
                found[i] = ("block" if blocked else "warn", req.owner or ctx.fields.owner, {
                    "column": req.column, "required_from": req.required_from.isoformat(), "by": req.by,
                    req.by: dated[i].date().isoformat(), "coverage": "required" if blocked else "not_covered",
                    "in_extract": req.column in ctx.mapped, "dictionary_version": ctx.fields.version,
                    "reason": reason, "fix": req.fix if blocked else req.before})
        rows = sorted(found)
        return Violations(rows, [found[i][2] for i in rows], severities=[found[i][0] for i in rows],
                          owners=[found[i][1] for i in rows])

