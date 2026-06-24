import { useEffect, useState } from "react";
import { useMsal } from "@azure/msal-react";
import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { graphRequest } from "../authConfig";

/**
 * Displays the signed-in user's name and profile photo (from Microsoft Graph)
 * in the top-right of the app header.
 */
export default function UserProfile() {
  const { instance, accounts } = useMsal();
  const account = accounts[0];
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);

  const name = account?.name ?? account?.username ?? "";
  const initials = name
    .split(" ")
    .map((part) => part.charAt(0))
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;

    async function loadPhoto() {
      if (!account) return;
      try {
        const result = await instance.acquireTokenSilent({
          ...graphRequest,
          account,
        });
        const response = await fetch(
          "https://graph.microsoft.com/v1.0/me/photo/$value",
          { headers: { Authorization: `Bearer ${result.accessToken}` } }
        );
        if (!response.ok) return;
        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setPhotoUrl(objectUrl);
      } catch (error) {
        if (error instanceof InteractionRequiredAuthError) {
          await instance.acquireTokenRedirect(graphRequest);
        }
        // No photo available or token failure: fall back to initials.
      }
    }

    loadPhoto();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [account, instance]);

  if (!account) return null;

  return (
    <div className="flex items-center gap-3">
      <span className="text-sm font-medium text-ms-text hidden sm:block">
        {name}
      </span>
      {photoUrl ? (
        <img
          src={photoUrl}
          alt={name}
          className="h-9 w-9 rounded-full object-cover border border-ms-border"
        />
      ) : (
        <div className="h-9 w-9 rounded-full bg-ms-blue text-white flex items-center justify-center text-sm font-semibold">
          {initials}
        </div>
      )}
    </div>
  );
}
