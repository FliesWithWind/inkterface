import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";
import { __mock } from "./mocks/decky-api";
import { resetUi } from "./mocks/decky-ui";

beforeEach(() => {
  __mock.reset();
  resetUi();
});
afterEach(() => cleanup());
