## KAgent Documentation

### Helm-Values
You can go [here](https://raw.githubusercontent.com/kagent-dev/kagent/refs/heads/main/helm/kagent/values.yaml) for the available helm values


## LLM Providers
Here is an example og the LLM Providers
```yaml
# ==============================================================================
# LLM PROVIDERS CONFIGURATION
# ==============================================================================
# https://kagent.dev/docs/getting-started/configuring-providers

providers:
  default: openAI
  openAI:
    provider: OpenAI
    model: "gpt-4.1-mini"
    apiKeySecretRef: kagent-openai
    apiKeySecretKey: OPENAI_API_KEY
    # apiKey: ""
  ollama:
    provider: Ollama
    model: "llama3.2"
    config:
      host: host.docker.internal:11434
  anthropic:
    provider: Anthropic
    model: "claude-3-5-haiku-20241022"
    apiKeySecretRef: kagent-anthropic
    apiKeySecretKey: ANTHROPIC_API_KEY
    # apiKey: ""
  azureOpenAI:
    provider: AzureOpenAI
    model: "gpt-4.1-mini"
    apiKeySecretRef: kagent-azure-openai
    apiKeySecretKey: AZUREOPENAI_API_KEY
    # apiKey: ""
    config:
      apiVersion: "2023-05-15"
      azureAdToken: ""
      azureDeployment: ""
      azureEndpoint: ""
  gemini:
    provider: Gemini
    model: "gemini-2.0-flash-lite"
    apiKeySecretRef: kagent-gemini
    apiKeySecretKey: GOOGLE_API_KEY
    # apiKey: ""
  ```

  ### Model Config Template
  ```YAML
  {{- $dot := . }}
{{- $defaultProfider := .Values.providers.default | default "openAI" }}
{{- $model := index .Values.providers $defaultProfider }}
{{- if hasKey .Values.providers  $defaultProfider | not }}
{{- fail  (printf "Provider key=%s is not found under .Values.providers" $defaultProfider)  }}
{{- end }}
---
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: {{ include "kagent.defaultModelConfigName" $dot | quote }}
  namespace: {{ include "kagent.namespace" . }}
  labels:
    {{- include "kagent.labels" $dot | nindent 4 }}
spec:
  {{- with $model }}
  provider: {{ .provider | quote }}
  model: {{ .model | quote }}
  {{- if $model.apiKeySecretRef }}
  apiKeySecret: {{.apiKeySecretRef}}
  {{- end }}
  {{- if $model.apiKeySecretKey }}
  apiKeySecretKey: {{.apiKeySecretKey}}
  {{- end }}
  {{- if hasKey $model "defaultHeaders" }}
  defaultHeaders:
    {{- toYaml $model.defaultHeaders | nindent 4 }}
  {{- end }}
  {{- if $model.config }}
  {{ $dot.Values.providers.default }}:
  {{- toYaml $model.config | nindent 4 }}
  {{- end }}
  {{- end }}
```
