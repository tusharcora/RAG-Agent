"use client";

import { useEffect, useState } from "react";
import { getSetupStatus, type SetupStatus } from "@/lib/setup";
import { Welcome } from "@/components/setup/Welcome";
import { OllamaStep } from "@/components/setup/OllamaStep";
import { ModelsStep } from "@/components/setup/ModelsStep";
import { KeysStep } from "@/components/setup/KeysStep";
import { Finishing } from "@/components/setup/Finishing";

type Step = "welcome" | "ollama" | "models" | "keys" | "finishing";

export default function SetupPage() {
  const [step, setStep] = useState<Step | null>(null);
  const [status, setStatus] = useState<SetupStatus | null>(null);

  // Skip ahead when Ollama is already up.
  useEffect(() => {
    getSetupStatus()
      .then((s) => {
        setStatus(s);
        setStep(s.ollama.running ? "models" : "welcome");
      })
      .catch(() => setStep("welcome"));
  }, []);

  if (step === null) return null;

  switch (step) {
    case "welcome":
      return <Welcome onLocal={() => setStep("ollama")} onKeys={() => setStep("keys")} />;
    case "ollama":
      return (
        <OllamaStep
          onBack={() => setStep("welcome")}
          onKeys={() => setStep("keys")}
          onRunning={() => {
            getSetupStatus()
              .then((s) => {
                setStatus(s);
                setStep("models");
              })
              .catch(() => {});
          }}
        />
      );
    case "models":
      return status ? <ModelsStep initial={status} onDone={() => setStep("finishing")} /> : null;
    case "keys":
      return <KeysStep onBack={() => setStep("welcome")} onDone={() => setStep("finishing")} />;
    case "finishing":
      return <Finishing />;
  }
}
