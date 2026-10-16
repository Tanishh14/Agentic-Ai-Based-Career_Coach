/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The browser talks to the FastAPI backend directly (NEXT_PUBLIC_API_BASE, CORS restricted to this origin).
};

module.exports = nextConfig;
