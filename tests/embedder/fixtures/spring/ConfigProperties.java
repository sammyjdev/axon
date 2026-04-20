package com.example.demo.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "prometheus")
public record ConfigProperties(
        String vaultPath,
        String enginePath,
        Database database,
        Ollama ollama,
        Budget budget
) {
    public record Database(String url, String user, String password) {}

    public record Ollama(String baseUrl, String primaryModel, String knowledgeModel) {
        public boolean hasKnowledgeModel() {
            return knowledgeModel != null && !knowledgeModel.isBlank();
        }
    }

    public record Budget(double dailyUsd, double opusUsd) {
        public boolean isOpusAllowed(double spent) {
            return spent < opusUsd;
        }
    }
}
