import { Children, isValidElement, type ReactNode } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";

interface ChoiceProps {
  value: string;
  children: ReactNode;
  disabled?: boolean;
}

// Declarative choices are converted to Radix items; no native select is rendered.
export function Choice(_props: ChoiceProps) {
  return null;
}

export function SelectField({
  value,
  onValueChange,
  children,
  disabled,
  required,
  ariaLabel,
}: {
  value: string;
  onValueChange: (value: string) => void;
  children: ReactNode;
  disabled?: boolean;
  required?: boolean;
  ariaLabel?: string;
}) {
  const choices = Children.toArray(children).filter(isValidElement<ChoiceProps>);
  const placeholder = choices.find((choice) => !choice.props.value)?.props.children;
  return (
    <Select
      value={value}
      onValueChange={onValueChange}
      disabled={disabled}
      required={required}
    >
      <SelectTrigger className="w-full" aria-label={ariaLabel}>
        <SelectValue placeholder={placeholder || "Select an option"} />
      </SelectTrigger>
      <SelectContent position="popper">
        {choices
          .filter((choice) => choice.props.value)
          .map((choice) => (
            <SelectItem
              key={choice.props.value}
              value={choice.props.value}
              disabled={choice.props.disabled}
            >
              {choice.props.children}
            </SelectItem>
          ))}
      </SelectContent>
    </Select>
  );
}
