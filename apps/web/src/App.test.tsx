import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import App from "./App";

it("renders_app_shell", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "ИПС — поддержка" })).toBeTruthy();
});
