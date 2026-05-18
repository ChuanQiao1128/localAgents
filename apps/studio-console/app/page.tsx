import { redirect } from "next/navigation";

/**
 * Root route — always redirect to Dashboard.
 * The Console has no "home" concept; Dashboard IS the home.
 */
export default function RootPage(): never {
  redirect("/dashboard");
}
