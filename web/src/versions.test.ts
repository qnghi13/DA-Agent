import { describe, expect, it } from "vitest";
import type { DatasetVersion } from "./api";
import {
  addedFiles,
  canDiscard,
  earlierNewFilesText,
  isOlderVersion,
  isOpenVersion,
  missingDecisions,
  replaceDefaults,
  replaceOptions,
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
      ["c", { from: 1, to: 2 }],
      ["e", { from: 2, to: 3 }],
    ]);
  });

  it("counts a run without a version as version 1", () => {
    const runs = [{ id: "a" }, { id: "b", dataset_version: 1 }, { id: "c", dataset_version: 2 }];
    expect([...versionDividers(runs)]).toEqual([["c", { from: 1, to: 2 }]]);
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
    expect(addedFiles(versions, 1, 2)).toEqual(["C"]);
    expect(addedFiles(versions, 0, 1)).toEqual(["A", "B"]);
  });

  it("names every file added across a gap of versions, skipping a failed one", () => {
    const f = (stored_as: string) => ({ name: stored_as, stored_as, bytes: 1 });
    const versions = [
      version({ version: 1, files: [f("orders.csv")] }),
      version({ version: 2, files: [f("orders.csv"), f("orders_jul.csv")] }),
      version({
        version: 3,
        status: "failed",
        files: [f("orders.csv"), f("orders_jul.csv"), f("orders.csv.2")],
      }),
      version({ version: 4, files: [f("orders.csv"), f("orders_jul.csv"), f("products.csv")] }),
    ];
    expect(addedFiles(versions, 1, 4)).toEqual(["orders_jul.csv", "products.csv"]);
    expect(addedFiles(versions, 2, 4)).toEqual(["products.csv"]);
    expect(addedFiles(versions, 1, 3)).toEqual(["orders_jul.csv"]);
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

describe("earlierNewFilesText", () => {
  it("names the files of the same upload that already hold the rows", () => {
    expect(earlierNewFilesText({ earlier_new_files: ["dec.csv"] })).toBe(
      "Some of these rows are also in dec.csv, sent in this same upload.",
    );
    expect(earlierNewFilesText({ earlier_new_files: ["a.csv", "b.csv"] })).toBe(
      "Some of these rows are also in a.csv, b.csv, sent in this same upload.",
    );
  });

  it("says nothing when the rows repeat only older data", () => {
    expect(earlierNewFilesText({ earlier_new_files: [] })).toBeNull();
    expect(earlierNewFilesText({})).toBeNull();
  });
});

describe("a version left by a job that ended", () => {
  it("says the check stopped and can be discarded", () => {
    const stuck = version({ status: "checking", stuck: true });
    expect(statusLine(stuck)).toBe(
      "Checking the new files stopped before it finished. Discard them and add them again.",
    );
    expect(canDiscard(stuck)).toBe(true);
  });

  it("cannot be discarded while its job may still run", () => {
    expect(canDiscard(version({ status: "building", stuck: false }))).toBe(false);
    expect(canDiscard(version({ status: "checking" }))).toBe(false);
    expect(canDiscard(version({ status: "needs_choice" }))).toBe(true);
    expect(canDiscard(version({ status: "applied" }))).toBe(false);
  });
});

describe("replaceOptions", () => {
  const files = [
    { name: "Orders.csv", stored_as: "orders.csv", bytes: 1 },
    { name: "Items.csv", stored_as: "items.csv", bytes: 1 },
  ];
  const overlap = { kind: "key", rows: 3, examples: [] };
  const orders = { table: "orders", kind: "grown", new_rows: 5, overlaps: [overlap], files: ["orders.csv", "orders_2.csv"] } as never;
  const items = { table: "items", kind: "grown", new_rows: 5, overlaps: [overlap], files: ["items.csv", "items_2.csv", "items_3.csv"] } as never;

  it("lists only the dataset's files the table is read from", () => {
    expect(replaceOptions(orders, files)).toEqual([files[0]]);
    expect(replaceOptions({ ...(orders as object), files: undefined } as never, files)).toEqual([]);
  });

  it("picks the file when a table has only one to replace", () => {
    const more = [...files, { name: "Items 2.csv", stored_as: "items_2.csv", bytes: 1 }];
    expect(replaceDefaults([orders, items], more)).toEqual({ orders: "orders.csv" });
  });
});
