

const nextConfig = {
  async redirects() {
    return [
      { source: "/briefing", destination: "/automation", permanent: false },
    ];
  },
};

export default nextConfig;
