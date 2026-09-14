{{- define "name" -}}
{{- default $.Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
The `app:` label, and it stays `ghlore` after the 2026-09-14 rename to relore. It is
the Deployment/StatefulSet *selector*, which Kubernetes will not let an upgrade
change: editing it makes `helm upgrade` fail on an immutable field, and forcing it
through orphans the running pods. The live release, its namespace and this label are
the deployment's old name; everything a reader or a client touches is the new one.
*/}}
{{- define "app.name" -}}
ghlore
{{- end -}}

{{- define "labels.standard" -}}
release: {{ $.Release.Name | quote }}
heritage: {{ $.Release.Service | quote }}
chart: "{{ include "name" . }}"
app: "{{ include "app.name" . }}"
{{- end -}}

{{/*
The database URL every workload shares. Assembled here rather than in values so the
password comes from the Secret at runtime and never appears in a rendered manifest, a
values file, or `helm get values`. build-plan docs/SECRETS-equivalent: config is tracked,
credentials are not.
*/}}
{{- define "relore.databaseUrl" -}}
postgresql+psycopg://{{ .Values.postgres.user }}:$(POSTGRES_PASSWORD)@{{ include "name" . }}-postgres:5432/{{ .Values.postgres.database }}
{{- end -}}

{{/*
The verbosity flag, as argv entries. `-v` is a top-level flag on `relored`, so it
has to precede the subcommand -- `relored -v poll`, never `relored poll -v`.
*/}}
{{- define "relore.verbosity" -}}
{{- if eq (int .Values.verbosity) 1 }}"-v", {{ else if ge (int .Values.verbosity) 2 }}"-vv", {{ end }}
{{- end -}}
