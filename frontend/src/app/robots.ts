import type { MetadataRoute } from "next"

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ['/', '/about', '/network', '/privacy'],
        disallow: ['/chat', '/room/'],
      },
    ],
    sitemap: "https://hybro.ai/sitemap.xml",
  }
}
