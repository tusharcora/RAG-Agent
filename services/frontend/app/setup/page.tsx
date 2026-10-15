"use client";

import { useEffect, useState } from "react";
import { getSetupStatus, type SetupStatus } from "@/lib/setup";
import { Welcome } from "@/components/setup/Welcome";
import { OllamaStep } from "@/components/setup/OllamaStep";
import { ModelsStep } from "@/components/setup/ModelsStep";
import { KeysStep } from "@/components/setup/KeysStep";
import { AccountStep } from "@/components/setup/AccountStep";
import { getAccount } from "@/lib/connect";
import { useAuth } from "@/lib/auth-context";
import { Finishing } from "@/components/setup/Finishing";

type Step = "account" | "welcome" | "ollama" | "models" | "keys" | "finishing";

export default function SetupPage() {
  const [step, setStep] = useState<Step | null>(null);
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const { refreshAccount, loading } = useAuth();

  // Optional sign-in comes first when the sign-in service is on and hasn't been offered yet;
  // otherwise skip ahead when Ollama is already up.
  useEffect(() => {
    // The session cookie is issued by AuthProvider's first load; /account needs it.
    if (loading) return;
    (async () => {
      const account = await getAccount().catch(() => null);
      let s: SetupStatus | null = null;
      try {
        s = await getSetupStatus();
        setStatus(s);
      } catch {
        // fall through to welcome
      }
      const afterAccount: Step = s?.ollama.running ? "models" : "welcome";
      setStep(account?.broker_configured && !account.prompt_seen && !s?.setup_complete ? "account" : afterAccount);
    })();
  }, [loading]);

  if (step === null) return null;

  switch (step) {
    case "account":
      return (
        <AccountStep
          onDone={() => {
            void refreshAccount();
            setStep(status?.ollama.running ? "models" : "welcome");
          }}
        />
      );
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
