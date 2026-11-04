import type { ReactNode } from "react";

import { UseCaseFrame } from "@/components/usecases/UseCaseFrame";

// The shared frame for /usecases/supermarket, /usecases/warehouse and /usecases/crowd: the
// world's promise, the three-world switcher, the live status and the journey stepper. Each page
// below it brings its own live data.
export default function UseCaseLayout({ children }: { children: ReactNode }) {
  return <UseCaseFrame>{children}</UseCaseFrame>;
}
