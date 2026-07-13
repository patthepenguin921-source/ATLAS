"use client";

// Minimal Google Drive Picker integration. Loads the Google Identity Services
// + Picker scripts on demand, prompts the user to authorize read-only Drive
// access, and resolves with the picked file plus a short-lived OAuth token that
// the backend uses to download the bytes.

export const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";
export const GOOGLE_API_KEY = process.env.NEXT_PUBLIC_GOOGLE_API_KEY ?? "";

export const driveConfigured = () => Boolean(GOOGLE_CLIENT_ID && GOOGLE_API_KEY);

export interface PickedFile {
  id: string;
  name: string;
  mimeType: string;
  accessToken: string;
}

declare global {
  interface Window {
    gapi?: any;
    google?: any;
  }
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) {
      resolve();
      return;
    }
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.defer = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(s);
  });
}

async function getAccessToken(): Promise<string> {
  await loadScript("https://accounts.google.com/gsi/client");
  return new Promise((resolve, reject) => {
    const client = window.google.accounts.oauth2.initTokenClient({
      client_id: GOOGLE_CLIENT_ID,
      scope: "https://www.googleapis.com/auth/drive.readonly",
      callback: (resp: any) => {
        if (resp.error) reject(new Error(resp.error));
        else resolve(resp.access_token);
      },
    });
    client.requestAccessToken();
  });
}

function loadPicker(): Promise<void> {
  return new Promise(async (resolve, reject) => {
    try {
      await loadScript("https://apis.google.com/js/api.js");
      window.gapi.load("picker", { callback: () => resolve() });
    } catch (e) {
      reject(e);
    }
  });
}

/** Opens the Google Picker and resolves with the chosen file, or null if canceled. */
export async function pickFromDrive(): Promise<PickedFile | null> {
  if (!driveConfigured()) {
    throw new Error("Google Drive is not configured.");
  }
  const accessToken = await getAccessToken();
  await loadPicker();

  return new Promise((resolve) => {
    const google = window.google;
    const view = new google.picker.DocsView(google.picker.ViewId.DOCS)
      .setIncludeFolders(true)
      .setSelectFolderEnabled(false);
    const picker = new google.picker.PickerBuilder()
      .addView(view)
      .setOAuthToken(accessToken)
      .setDeveloperKey(GOOGLE_API_KEY)
      .setCallback((data: any) => {
        if (data.action === google.picker.Action.PICKED) {
          const doc = data.docs[0];
          resolve({
            id: doc.id,
            name: doc.name,
            mimeType: doc.mimeType,
            accessToken,
          });
        } else if (data.action === google.picker.Action.CANCEL) {
          resolve(null);
        }
      })
      .build();
    picker.setVisible(true);
  });
}
