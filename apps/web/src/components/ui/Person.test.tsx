import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Person, { personLabel } from "./Person";

const html = (name: string | null, email = "ada@example.test") => renderToStaticMarkup(<Person name={name} email={email} />);

describe("Person", () => {
  it("renders the name first and the email in its own element", () => {
    const out = html("Ada Lovelace");
    expect(out.indexOf("Ada Lovelace")).toBeLessThan(out.indexOf("ada@example.test"));
    expect(out).toContain('<span class="person-name">Ada Lovelace</span><span class="person-email">ada@example.test</span>');
    expect(out).toContain('title="Ada Lovelace ada@example.test"');
  });
  it.each([null, "", "   "])("renders the email alone for name %j", (name) => {
    const out = html(name);
    expect(out).not.toContain("person-name");
    expect(out).toContain("<span>ada@example.test</span>");
    expect(out).toContain('title="ada@example.test"');
  });
  it("personLabel gives Name (email) or the bare email", () => {
    expect(personLabel("Ada Lovelace", "ada@example.test")).toBe("Ada Lovelace (ada@example.test)");
    expect(personLabel(null, "ada@example.test")).toBe("ada@example.test");
    expect(personLabel("  ", "ada@example.test")).toBe("ada@example.test");
  });
});
