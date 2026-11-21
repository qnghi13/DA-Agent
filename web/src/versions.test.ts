import { describe, expect, it } from "vitest";
import type { DatasetVersion } from "./api";
import {
  addedFiles,
  isOlderVersion,
  isOpenVersion,
  missingDecisions,
  statusLine,
  versionDividers,
} from "./versions";

function version(patch: Partial<DatasetVersion> = {}): DatasetVersion {
  return {
    version: 2,
    status: "applied",
    files: [],
    spec: null,
    summary: null,
    changes: null,
    choices: null,
    repairs: null,
    error: null,
    created_at: "2026-11-21T00:00:00Z",
    ...patch,
  };
}

const change = { table: "orders", kind: "grown" as const, new_rows: 1240 };

describe("versionDividers", () => {
  it("puts a divider before the first run on each newer version", () => {
    const runs = [
      { id: "a", dataset_version: 1 },
      { id: "b", dataset_version: 1 },
      { id: "c", dataset_version: 2 },
      { id: "d", dataset_version: 2 },
      { id: "e", dataset_version: 3 },
    ];
    expect([...versionDividers(runs)]).toEqual([
      ["c", 2],
      ["e", 3],
    ]);
  });

  it("counts a run without a version as version 1", () => {
    const runs = [{ id: "a" }, { id: "b", dataset_version: 1 }, { id: "c", dataset_version: 2 }];
    expect([...versionDividers(runs)]).toEqual([["c", 2]]);
  });

  it("shows nothing for no runs", () => {
    expect(versionDividers([]).size).toBe(0);
  });
});

describe("isOlderVersion", () => {
  it("marks runs below the current version, a missing version being 1", () => {
    expect(isOlderVersion({ dataset_version: 1 }, 2)).toBe(true);
    expect(isOlderVersion({}, 2)).toBe(true);
    expect(isOlderVersion({ dataset_version: 2 }, 2)).toBe(false);
    expect(isOlderVersion({}, 1)).toBe(false);
  });
});

describe("statusLine", () => {
  it("says the files are being checked while the version is new", () => {
    expect(statusLine(version({ status: "uploaded" }))).toBe("Checking new files…");
    expect(statusLine(version({ status: "checking" }))).toBe("Checking new files…");
  });

  it("asks for a decision when the version waits for one", () => {
    expect(statusLine(version({ status: "needs_choice" }))).toBe("Your new files need a decision");
    expect(statusLine(version({ status: "proposed" }))).toBe("Your new files need a decision");
  });

  it("reports the rows added and the parse rate from the summary", () => {
    const summary = {
      tables: [
        {
          name: "orders",
          rows: 5000,
          row: "",
          columns: 2,
          profile: [
            { name: "a", filled: 900, parsed: 900 },
            { name: "b", filled: 100, parsed: 99 },
          ],
        },
      ],
    };
    const line = statusLine(
      version({ summary: summary as unknown as DatasetVersion["summary"], changes: [change] as never }),
    );
    expect(line).toBe("Added 1,240 rows to orders (parse rate 99.9%)");
  });

  it("leaves the parse rate out when the summary has none", () => {
    expect(statusLine(version({ changes: [change] as never }))).toBe("Added 1,240 rows to orders");
  });

  it("reports the failure reason", () => {
    expect(statusLine(version({ status: "failed", error: "too many columns" }))).toBe(
      "Couldn't add files: too many columns",
    );
  });
});

describe("isOpenVersion", () => {
  it("is open until it is applied, failed, or discarded", () => {
    expect(isOpenVersion(version({ status: "building" }))).toBe(true);
    expect(isOpenVersion(version({ status: "applied" }))).toBe(false);
    expect(isOpenVersion(version({ status: "failed" }))).toBe(false);
  });
});

describe("addedFiles", () => {
  it("names the files a version has and the one before it lacks", () => {
    const f = (stored_as: string) => ({ name: stored_as.toUpperCase(), stored_as, bytes: 1 });
    const versions = [
      version({ version: 1, files: [f("a"), f("b")] }),
      version({ version: 2, files: [f("a"), f("c")] }),
    ];
    expect(addedFiles(versions, 2)).toEqual(["C"]);
    expect(addedFiles(versions, 1)).toEqual(["A", "B"]);
  });
});

describe("missingDecisions", () => {
  const overlap = { kind: "key", rows: 3, examples: [] };
  const changes = [
    { table: "orders", kind: "grown", new_rows: 5, overlaps: [overlap], allowed_choices: ["replace", "keep_both"] },
    { table: "items", kind: "grown", new_rows: 5, overlaps: [{ ...overlap, removed_by_dedupe: true }] },
  ] as never;

  it("needs a choice for each table with an overlap that is not already handled", () => {
    expect(missingDecisions(changes, {}, {})).toEqual(["orders"]);
    expect(missingDecisions(changes, { orders: "keep_both" }, {})).toEqual([]);
  });

  it("needs a file for a replace choice", () => {
    expect(missingDecisions(changes, { orders: "replace" }, {})).toEqual(["orders"]);
    expect(missingDecisions(changes, { orders: "replace" }, { orders: "a.csv" })).toEqual([]);
  });
});
