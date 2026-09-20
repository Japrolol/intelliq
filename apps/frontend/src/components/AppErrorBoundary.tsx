import { Component, type ReactNode } from "react";
import { Button, Card } from "./ui";

/** Keep unexpected rendering failures recoverable without exposing tenant data. */
export class AppErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;

    return (
      <main className="flex min-h-screen items-center justify-center bg-background p-6">
        <Card className="w-full max-w-lg p-8">
          <div role="alert" className="space-y-5">
            <h1 className="text-2xl font-semibold">This view could not be displayed.</h1>
            <p className="text-sm leading-6 text-muted-foreground">
              Reload to reconnect. Before retrying an action, check its execution receipt;
              a display error does not mean an approved change failed.
            </p>
            <Button onClick={() => window.location.reload()}>Reload IntelliQ</Button>
          </div>
        </Card>
      </main>
    );
  }
}
