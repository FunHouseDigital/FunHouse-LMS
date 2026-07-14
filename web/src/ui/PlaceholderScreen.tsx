/**
 * Placeholder screen used for the role-gated routes until the real capture and
 * read screens are built (tasks.md tasks 11–12). The routing, guard, and nav
 * are real and tested now; only the screen bodies are stand-ins.
 */
export interface PlaceholderScreenProps {
  title: string;
  screenId: string;
}

export function PlaceholderScreen({ title, screenId }: PlaceholderScreenProps) {
  return (
    <section aria-label={title} data-screen-body={screenId}>
      <h1>{title}</h1>
      <p>This screen is coming soon.</p>
    </section>
  );
}

export default PlaceholderScreen;
