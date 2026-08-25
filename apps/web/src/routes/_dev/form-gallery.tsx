import { CheckCircle } from "@phosphor-icons/react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { apiClient } from "../../api/client";
import { applyProblemErrors } from "../../api/client/problems";
import type { components } from "../../api/generated/schema";
import Combobox from "../../components/form/Combobox";
import ErrorSummary, { type SummaryError } from "../../components/form/ErrorSummary";
import Field from "../../components/form/Field";
import Input from "../../components/form/Input";
import MoneyInput from "../../components/form/MoneyInput";
import Select from "../../components/form/Select";
import Surface from "../../components/ui/Surface";

type LoginRequest = components["schemas"]["LoginRequest"];

const emailSchema = z
  .string()
  .min(1, "Email is required. Enter the email address for your account.")
  .email("The email address is incomplete. Enter it in the format name@example.com.");
const passwordSchema = z
  .string()
  .min(1, "Password is required. Enter the current password for your account.");
const loginSchema = z.object({
  email: emailSchema,
  password: passwordSchema,
}) satisfies z.ZodType<LoginRequest>;

const formSchema = loginSchema.extend({
  approvalMode: z.string().min(1, "Approval mode is required. Choose one of the listed modes."),
  budget: z
    .string()
    .regex(
      /^(?:0|[1-9]\d*)(?:\.\d{1,4})?$/,
      "Budget format is invalid. Enter a positive amount with no more than four decimal places.",
    )
    .refine(
      (value) => !/^0(?:\.0{1,4})?$/.test(value),
      "Budget must be greater than zero. Enter a positive amount and try again.",
    ),
  reviewer: z.string().min(1, "Reviewer is required. Choose a reviewer from the suggestions."),
});

type FormValues = z.infer<typeof formSchema>;

const REVIEWERS = [
  { label: "Avery Chen", value: "Avery Chen" },
  { label: "Jordan Singh", value: "Jordan Singh" },
  { label: "Morgan Lee", value: "Morgan Lee" },
] as const;

function validateOnBlur(schema: z.ZodType<string>) {
  return (value: string) => {
    const result = schema.safeParse(value);
    return result.success ? true : (result.error.issues[0]?.message ?? "Correct this field.");
  };
}

function summaryItem(fieldId: string, label: string, message: unknown): SummaryError | null {
  return typeof message === "string" ? { fieldId, label, message } : null;
}

export default function FormGallery() {
  const [submitted, setSubmitted] = useState(false);
  const {
    clearErrors,
    formState: { errors, isSubmitting },
    handleSubmit,
    register,
    setError,
  } = useForm<FormValues>({
    defaultValues: { approvalMode: "", budget: "", email: "", password: "", reviewer: "" },
    mode: "onBlur",
    reValidateMode: "onBlur",
    shouldFocusError: true,
  });

  const summaryErrors = [
    summaryItem("form-email", "Email", errors.email?.message),
    summaryItem("form-password", "Password", errors.password?.message),
    summaryItem("form-approval-mode", "Approval mode", errors.approvalMode?.message),
    summaryItem("form-reviewer", "Reviewer", errors.reviewer?.message),
    summaryItem("form-budget", "Budget", errors.budget?.message),
  ].filter((error): error is SummaryError => error !== null);

  const submit = handleSubmit(async (values) => {
    setSubmitted(false);
    clearErrors("root");
    const result = await apiClient.POST("/api/v1/auth/login", {
      body: { email: values.email, password: values.password },
    });

    if (result.response.ok) {
      setSubmitted(true);
      return;
    }
    if (applyProblemErrors<FormValues>(result.error, setError)) return;
    setError("root.server", {
      message:
        result.error?.detail ??
        "The form could not be submitted. Your entries were preserved; check your connection and try again.",
      type: "server",
    });
  });

  return (
    <Surface as="section" aria-labelledby="form-kit-heading" className="form-kit" shadow="md">
      <div className="form-kit-intro">
        <h2 id="form-kit-heading">Accessible form primitives</h2>
        <p>
          Required fields keep their instructions visible. Validation runs after leaving a field,
          and the server can return errors to any generated field path.
        </p>
      </div>
      <form noValidate onSubmit={submit}>
        <ErrorSummary errors={summaryErrors} />
        {errors.root?.server?.message === undefined ? undefined : (
          <p className="form-server-error" role="alert">
            {errors.root.server.message}
          </p>
        )}
        <Field
          error={errors.email?.message}
          help="Use the address associated with your XLR8 FLO account."
          id="form-email"
          label="Email"
          required
        >
          <Input
            autoComplete="email"
            inputMode="email"
            type="email"
            {...register("email", { validate: validateOnBlur(emailSchema) })}
          />
        </Field>
        <Field
          error={errors.password?.message}
          help="Use your current password. It is never shown after entry."
          id="form-password"
          label="Password"
          required
        >
          <Input
            autoComplete="current-password"
            inputMode="text"
            type="password"
            {...register("password", { validate: validateOnBlur(passwordSchema) })}
          />
        </Field>
        <Field
          error={errors.approvalMode?.message}
          help="Choose how this fixture should demonstrate a native select."
          id="form-approval-mode"
          label="Approval mode"
          required
        >
          <Select
            {...register("approvalMode", {
              validate: validateOnBlur(formSchema.shape.approvalMode),
            })}
          >
            <option value="">Choose a mode</option>
            <option value="single">Single approver</option>
            <option value="sequence">Approval sequence</option>
          </Select>
        </Field>
        <Field
          error={errors.reviewer?.message}
          help="Start typing a name, then choose a matching reviewer."
          id="form-reviewer"
          label="Reviewer"
          required
        >
          <Combobox
            autoComplete="off"
            inputMode="text"
            options={REVIEWERS}
            {...register("reviewer", { validate: validateOnBlur(formSchema.shape.reviewer) })}
          />
        </Field>
        <Field
          error={errors.budget?.message}
          help="Enter the full amount in USD with up to four decimal places."
          id="form-budget"
          label="Budget"
          required
        >
          <MoneyInput
            autoComplete="off"
            currency="USD"
            {...register("budget", { validate: validateOnBlur(formSchema.shape.budget) })}
          />
        </Field>
        <button className="form-submit" disabled={isSubmitting} type="submit">
          {isSubmitting ? "Submitting…" : "Submit fixture"}
        </button>
        {submitted ? (
          <p className="form-success" role="status">
            <CheckCircle aria-hidden="true" weight="regular" />
            Form submitted. Every value remains available for the next edit.
          </p>
        ) : undefined}
      </form>
    </Surface>
  );
}
