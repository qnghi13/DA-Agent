import { describe, expect, it } from "vitest";
import { loginToken, sessionStarter, viewFor } from "./auth";

describe("loginToken", () => {
  it("reads the token from a login link's fragment", () => {
    const token = `dal_${"a".repeat(43)}`;
    expect(loginToken({ pathname: "/login", hash: `#token=${token}` })).toBe(token);
  });

  it("ignores other pages and malformed fragments", () => {
    expect(loginToken({ pathname: "/", hash: "#token=dal_x" })).toBeNull();
    expect(loginToken({ pathname: "/login", hash: "" })).toBeNull();
    expect(loginToken({ pathname: "/login", hash: "#token=" })).toBeNull();
    expect(loginToken({ pathname: "/login", hash: "#other=1" })).toBeNull();
  });
});

describe("viewFor", () => {
  it("sends the operator to the dashboard and members to their workspace", () => {
    expect(viewFor({ role: "operator", workspace_id: null, workspace_name: null })).toBe("ops");
    expect(viewFor({ role: "member", workspace_id: "w", workspace_name: "Shop" })).toBe(
      "workspace",
    );
  });
});

describe("sessionStarter", () => {
  const token = `dal_${"b".repeat(43)}`;
  const me = { role: "member", workspace_id: "w", workspace_name: "Shop" } as const;

  it("redeems a link once even when the page starts twice, and clears it from the address", async () => {
    let redeemed = 0;
    let checked = 0;
    const replaced: string[] = [];
    const start = sessionStarter({
      location: { pathname: "/login", hash: `#token=${token}` },
      replace: (path) => replaced.push(path),
      redeem: async () => {
        redeemed += 1;
        return me;
      },
      me: async () => {
        checked += 1;
        throw new Error("401");
      },
    });

    const [first, second] = await Promise.all([start(), start()]);

    expect(first).toEqual({ me, fromLink: true });
    expect(second).toEqual(first);
    expect([redeemed, checked]).toEqual([1, 0]);
    expect(replaced).toEqual(["/"]);
  });

  it("asks who is signed in when the page was not opened from a link", async () => {
    const start = sessionStarter({
      location: { pathname: "/", hash: "" },
      replace: () => undefined,
      redeem: async () => me,
      me: async () => me,
    });

    expect(await start()).toEqual({ me, fromLink: false });
  });

  it("says when a link failed, so the page can explain it", async () => {
    const start = sessionStarter({
      location: { pathname: "/login", hash: `#token=${token}` },
      replace: () => undefined,
      redeem: async () => {
        throw new Error("401");
      },
      me: async () => me,
    });

    await expect(start()).rejects.toMatchObject({ fromLink: true });
  });
});
