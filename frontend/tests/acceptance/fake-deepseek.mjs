import http from "node:http";

const port = Number(process.env.FAKE_DEEPSEEK_PORT || "19101");

function readBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
    });
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

function responseFor(payload) {
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  const system = messages.find((message) => message?.role === "system")?.content || "";
  const systemText = String(system).toLowerCase();
  const userText = messages
    .filter((message) => message?.role === "user")
    .map((message) => String(message.content || ""))
    .join("\n")
    .toLowerCase();

  if (systemText.includes("initial rental enquiry")) {
    const isRental = /\b(rent|rental|available|viewing|unit|listing|property)\b/.test(userText);
    return {
      is_initial_rental_enquiry: isRental,
      confidence: "high",
      reason: isRental ? "deterministic rental enquiry" : "deterministic non-rental enquiry",
    };
  }

  if (systemText.includes("property list")) {
    const properties = parsePropertyList(system);
    const matched = properties.find((property) => {
      const name = String(property.property_name || "").toLowerCase();
      const id = String(property.property_id || "").toLowerCase();
      return (name && userText.includes(name)) || (id && userText.includes(id));
    });

    if (!matched) {
      return {
        match_status: "no_property_mentioned",
        mentioned_property_raw: "",
        mentioned_listing_url: "",
        extracted_listing_id: "",
        matched_by: "none",
        matched_properties: [],
        reason: "deterministic no match",
      };
    }

    return {
      match_status: "matched",
      mentioned_property_raw: matched.property_name,
      mentioned_listing_url: "",
      extracted_listing_id: "",
      matched_by: "property_name",
      matched_properties: [
        {
          property_id: matched.property_id,
          property_name: matched.property_name,
          reason: "deterministic property-name match",
        },
      ],
      reason: "single deterministic listing match",
    };
  }

  return {
    stage_status: "manual_review",
    reason: "unknown deterministic prompt",
  };
}

function parsePropertyList(systemPrompt) {
  const match = systemPrompt.match(/PROPERTY LIST:\s*"""\n([\s\S]*?)\n"""/);
  const propertyList = match?.[1]?.trim() || "";
  if (!propertyList) return [];
  return propertyList
    .split("\n")
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function chatCompletion(content) {
  return {
    id: "fake-deepseek-acceptance",
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model: "deepseek-reasoner",
    choices: [
      {
        index: 0,
        finish_reason: "stop",
        message: {
          role: "assistant",
          content: JSON.stringify(content),
        },
      },
    ],
    usage: {
      prompt_tokens: 1,
      completion_tokens: 1,
      total_tokens: 2,
    },
  };
}

const server = http.createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ ok: true }));
    return;
  }

  if (request.method === "POST" && request.url === "/chat/completions") {
    try {
      const body = JSON.parse(await readBody(request));
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify(chatCompletion(responseFor(body))));
    } catch (error) {
      response.writeHead(500, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: String(error) }));
    }
    return;
  }

  response.writeHead(404, { "content-type": "application/json" });
  response.end(JSON.stringify({ error: "not found" }));
});

server.listen(port, "127.0.0.1");

process.on("SIGTERM", () => server.close(() => process.exit(0)));
process.on("SIGINT", () => server.close(() => process.exit(0)));
