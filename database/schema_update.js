// MongoDB agent schema update
db.agents.updateMany({}, {
  $set: {
    is_remote: false,  // Default all existing agents to non-remote
    endpoint: null     // No endpoint for non-remote agents
  }
});

// Example document for remote agent
db.agents.insertOne({
  name: "Remote Specialist Agent",
  description: "A specialist agent hosted on external infrastructure",
  capabilities: ["specialized_task", "external_processing"],
  prompt: "You are a specialized remote agent...",
  model: "external",
  is_remote: true,
  endpoint: "https://example.com/agent-api",
  created_at: new Date(),
  updated_at: new Date()
}); 