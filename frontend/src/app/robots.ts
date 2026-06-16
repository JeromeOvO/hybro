import type { MetadataRoute } from "next"

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ["/", "/c/about", "/privacy", "/d/docs"],
        disallow: ["/c/chat", "/c/room/", "/d/agents/", "/d/inspector", "/d/register", "/d/discovery-api-keys"],
      },
    ],
    sitemap: "https://hybro.ai/sitemap.xml",
  }
}
