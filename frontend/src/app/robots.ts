import type { MetadataRoute } from "next"

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ['/', '/about', '/agents', '/privacy'],
        disallow: ['/chat', '/room/', '/manage/'],
      },
    ],
    sitemap: "https://hybro.ai/sitemap.xml",
  }
}
