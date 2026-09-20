import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "./ui/button";

export function BackToWorkspace({ label = "Back to projects" }: { label?: string }) {
  const navigate = useNavigate();

  return (
    <Button variant="ghost" size="sm" onClick={() => navigate("/projects")}>
      <ArrowLeft />
      {label}
    </Button>
  );
}
