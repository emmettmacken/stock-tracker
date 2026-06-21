

const nextConfig = {
  async redirects() {
    return [
      { source: "/portfolio", destination: "/strategy-lab", permanent: false },
      { source: "/briefing", destination: "/automation", permanent: false },
    ];
  },
};

export default nextConfig;
