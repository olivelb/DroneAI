/**
 * Resource-to-work-buffer modifier for DroneGS directional opacity.
 *
 * The four resource streams contain 16 float32 values per splat: the base
 * opacity logit and the 15 degree-1..3 coefficients. The result is baked into
 * PlayCanvas' ordinary color alpha whenever its unified renderer refreshes SH
 * color for a changed view. No custom data is retained in the work-buffer.
 */
export const DRONEGS_OPACITY_MODIFIER_GLSL = /* glsl */ `
uniform vec3 uDroneCameraPosition;

void modifySplatCenter(inout vec3 center) {}

void modifySplatRotationScale(
    vec3 originalCenter,
    vec3 modifiedCenter,
    inout vec4 rotation,
    inout vec3 scale
) {}

void modifySplatColor(vec3 center, inout vec4 color) {
    vec3 delta = center - uDroneCameraPosition;
    vec3 dir = delta / max(length(delta), 1.0e-12);
    float x = dir.x;
    float y = dir.y;
    float z = dir.z;
    float xx = x * x;
    float yy = y * y;
    float zz = z * z;

    vec4 c0 = loadDroneOpacity0();
    vec4 c1 = loadDroneOpacity1();
    vec4 c2 = loadDroneOpacity2();
    vec4 c3 = loadDroneOpacity3();

    float logit = c0.x;
    logit += c0.y * (-0.4886025119029199 * y);
    logit += c0.z * ( 0.4886025119029199 * z);
    logit += c0.w * (-0.4886025119029199 * x);
    logit += c1.x * ( 1.0925484305920792 * x * y);
    logit += c1.y * (-1.0925484305920792 * y * z);
    logit += c1.z * ( 0.31539156525252005 * (2.0 * zz - xx - yy));
    logit += c1.w * (-1.0925484305920792 * x * z);
    logit += c2.x * ( 0.5462742152960396 * (xx - yy));
    logit += c2.y * (-0.5900435899266435 * y * (3.0 * xx - yy));
    logit += c2.z * ( 2.890611442640554 * x * y * z);
    logit += c2.w * (-0.4570457994644658 * y * (4.0 * zz - xx - yy));
    logit += c3.x * ( 0.3731763325901154 * z * (2.0 * zz - 3.0 * xx - 3.0 * yy));
    logit += c3.y * (-0.4570457994644658 * x * (4.0 * zz - xx - yy));
    logit += c3.z * ( 1.445305721320277 * z * (xx - yy));
    logit += c3.w * (-0.5900435899266435 * x * (xx - 3.0 * yy));
    color.a = 1.0 / (1.0 + exp(-logit));
}
`;

export const DRONEGS_OPACITY_MODIFIER_WGSL = /* wgsl */ `
uniform uDroneCameraPosition: vec3f;

fn modifySplatCenter(center: ptr<function, vec3f>) {}

fn modifySplatRotationScale(
    originalCenter: vec3f,
    modifiedCenter: vec3f,
    rotation: ptr<function, vec4f>,
    scale: ptr<function, vec3f>
) {}

fn modifySplatColor(center: vec3f, color: ptr<function, vec4f>) {
    let delta = center - uniform.uDroneCameraPosition;
    let dir = delta / max(length(delta), 1.0e-12);
    let x = dir.x;
    let y = dir.y;
    let z = dir.z;
    let xx = x * x;
    let yy = y * y;
    let zz = z * z;

    let c0 = loadDroneOpacity0();
    let c1 = loadDroneOpacity1();
    let c2 = loadDroneOpacity2();
    let c3 = loadDroneOpacity3();

    var logit = c0.x;
    logit += c0.y * (-0.4886025119029199 * y);
    logit += c0.z * ( 0.4886025119029199 * z);
    logit += c0.w * (-0.4886025119029199 * x);
    logit += c1.x * ( 1.0925484305920792 * x * y);
    logit += c1.y * (-1.0925484305920792 * y * z);
    logit += c1.z * ( 0.31539156525252005 * (2.0 * zz - xx - yy));
    logit += c1.w * (-1.0925484305920792 * x * z);
    logit += c2.x * ( 0.5462742152960396 * (xx - yy));
    logit += c2.y * (-0.5900435899266435 * y * (3.0 * xx - yy));
    logit += c2.z * ( 2.890611442640554 * x * y * z);
    logit += c2.w * (-0.4570457994644658 * y * (4.0 * zz - xx - yy));
    logit += c3.x * ( 0.3731763325901154 * z * (2.0 * zz - 3.0 * xx - 3.0 * yy));
    logit += c3.y * (-0.4570457994644658 * x * (4.0 * zz - xx - yy));
    logit += c3.z * ( 1.445305721320277 * z * (xx - yy));
    logit += c3.w * (-0.5900435899266435 * x * (xx - 3.0 * yy));
    (*color).a = 1.0 / (1.0 + exp(-logit));
}
`;
