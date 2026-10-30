# The PDF renderer as a Lambda container image (arm64): reintro.pregen.handler.
# Built and deployed by .github/workflows/pdf-lambda.yml; docs/deploy.md has the setup.
FROM public.ecr.aws/lambda/python:3.13

# Pango for WeasyPrint, and the DejaVu faces the PDF's stylesheet names.
RUN dnf install -y pango dejavu-sans-fonts dejavu-serif-fonts \
    && dnf clean all

COPY pyproject.toml ${LAMBDA_TASK_ROOT}/src/
COPY reintro ${LAMBDA_TASK_ROOT}/src/reintro
RUN pip install --no-cache-dir "${LAMBDA_TASK_ROOT}/src" --target "${LAMBDA_TASK_ROOT}" \
    && rm -rf "${LAMBDA_TASK_ROOT}/src"

CMD ["reintro.pregen.handler"]
