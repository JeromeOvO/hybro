import type { MetadataRoute } from "next"

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ['/', '/core', '/about', '/agents', '/privacy'],
        disallow: ['/chat', '/room/', '/manage/'],
      },
    ],
    sitemap: "https://hybro.ai/sitemap.xml",
  }
}
