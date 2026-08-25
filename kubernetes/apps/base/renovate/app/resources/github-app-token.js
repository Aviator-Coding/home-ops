"use strict";

const crypto = require("crypto");
const https = require("https");

const APP_ID = process.env.GITHUB_APP_ID;
const RAW_KEY = process.env.GITHUB_APP_PRIVATE_KEY;
const ACCOUNT = process.env.GITHUB_APP_ACCOUNT || "Aviator-Coding";

if (!APP_ID || !RAW_KEY) {
  console.error("GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY must be set");
  process.exit(1);
}

const privateKey = RAW_KEY.replace(/\\n/g, "\n");

function b64url(input) {
  const buf = Buffer.isBuffer(input) ? input : Buffer.from(input);
  return buf.toString("base64url");
}

function mintJwt() {
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const payload = b64url(
    JSON.stringify({ iat: now - 60, exp: now + 540, iss: APP_ID }),
  );
  const signer = crypto.createSign("RSA-SHA256");
  signer.update(`${header}.${payload}`);
  const signature = signer.sign(privateKey, "base64url");
  return `${header}.${payload}.${signature}`;
}

function githubRequest(method, path, jwt) {
  return new Promise((resolve, reject) => {
    const req = https.request(
      {
        hostname: "api.github.com",
        path,
        method,
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${jwt}`,
          "User-Agent": "home-ops-renovate",
          "X-GitHub-Api-Version": "2022-11-28",
        },
      },
      (res) => {
        let body = "";
        res.on("data", (chunk) => {
          body += chunk;
        });
        res.on("end", () => {
          let parsed = {};
          if (body) {
            try {
              parsed = JSON.parse(body);
            } catch (err) {
              reject(
                new Error(
                  `GitHub ${method} ${path} returned non-JSON (${res.statusCode}): ${body.slice(0, 200)}`,
                ),
              );
              return;
            }
          }
          if (res.statusCode < 200 || res.statusCode >= 300) {
            reject(
              new Error(
                `GitHub ${method} ${path} failed (${res.statusCode}): ${body.slice(0, 500)}`,
              ),
            );
            return;
          }
          resolve(parsed);
        });
      },
    );
    req.on("error", reject);
    req.end();
  });
}

(async () => {
  const jwt = mintJwt();
  const installations = await githubRequest("GET", "/app/installations", jwt);
  if (!Array.isArray(installations) || installations.length === 0) {
    throw new Error("GitHub App has no installations");
  }
  const installation =
    installations.find((item) => item.account && item.account.login === ACCOUNT) ||
    (installations.length === 1 ? installations[0] : null);
  if (!installation) {
    const logins = installations
      .map((item) => (item.account && item.account.login) || "?")
      .join(", ");
    throw new Error(
      `No GitHub App installation for ${ACCOUNT}; found: ${logins}`,
    );
  }
  const token = await githubRequest(
    "POST",
    `/app/installations/${installation.id}/access_tokens`,
    jwt,
  );
  if (!token.token) {
    throw new Error("GitHub App installation token response missing token");
  }
  process.stdout.write(token.token);
})().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
