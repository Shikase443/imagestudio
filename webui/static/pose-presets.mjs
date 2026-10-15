const identity = 'Keep the same person, facial features, hairstyle, clothing design and background as the input image.';
export const posePresets = [
  {key:'pose',label:'ポーズ全体を合わせる',
    prompt:'画像1の人物を、画像2と同じ身体の向き・腕の高さ・肘の曲げ方・手の位置・脚の開き幅・膝の曲げ方に描き替えてください。顔立ち、髪型、服装のデザイン、背景は画像1を維持し、服の輪郭やしわは変更後の姿勢に合わせてください。',
    read:'body pose below the neck. Include torso facing direction, both arms and hand contact locations, leg spacing and knee bending',
    keep:'Redraw the body and clothing outline to match the new pose. Exactly two arms and two legs.'},
  {key:'pose-arms',label:'腕のみ合わせる',
    prompt:'画像1の両腕だけを画像2に合わせてください。腕の上げ下げ、肘の曲げ方、手の位置と接触先を再現してください。脚、胴体の向き、頭の向き、顔立ち、髪型、服装のデザイン、背景は画像1を維持してください。',
    read:'arms and hands only. Include each arm elevation, elbow bend and hand location or contact point. Distinguish image-left from image-right. Do not describe the legs, torso or head orientation',
    keep:'Change only the two arms and hands and the corresponding sleeves. Keep the leg pose, torso orientation and head orientation unchanged. Exactly two arms.'},
  {key:'pose-legs',label:'脚のみ合わせる',
    prompt:'画像1の両脚だけを画像2に合わせてください。脚の開き幅、左右の膝の曲がり、足先の位置と向きを再現してください。腕、胴体の向き、頭の向き、顔立ち、髪型、服装のデザイン、背景は画像1を維持してください。',
    read:'legs and feet only. Include stance width, each knee bend, and foot position and direction. Distinguish image-left from image-right. Do not describe the arms, torso or head orientation',
    keep:'Change only the two legs and feet and the corresponding clothing. Keep the arm pose, torso orientation and head orientation unchanged. Exactly two legs.'},
  {key:'pose-body',label:'体の向きのみ合わせる',
    prompt:'画像1の胸と骨盤の向き、胴体の傾きだけを画像2に合わせてください。腕や脚のポーズ、頭の向き、顔立ち、髪型、服装のデザイン、背景は画像1をできる限り維持してください。',
    read:'torso orientation only. Include the direction the chest and pelvis face (front, back, image-left, image-right or three-quarter) and the torso lean or twist. Do not describe the arm or leg pose or head orientation',
    keep:'Change only the torso facing direction, lean and twist. Preserve the existing arm and leg pose and head orientation as far as anatomically possible. Adapt clothing folds to the torso rotation.'},
  {key:'pose-head',label:'頭の向きのみ合わせる',
    prompt:'画像1の頭の向きだけを画像2に合わせてください。左右の向き、上向き・下向き、頭の傾きを再現してください。顔立ち、髪型、表情は画像1の人物を保ち、胴体、腕、脚、服装、背景は変更しないでください。',
    read:'head orientation only. Include which direction the face points (front, back, image-left, image-right or three-quarter), upward or downward tilt, and sideways head tilt. Do not describe facial features, expression, gaze, hair, body or clothing',
    keep:'Rotate only the head and neck to the target orientation while retaining the input person identity, facial features, hairstyle and expression. Keep the torso, arms, legs and clothing unchanged.'}
];
export function poseAnalysisPrompt(preset) {
  return `Describe only the main person's ${preset.read}. Write one concise English editing instruction starting with "Change the person to". Use spatial words, not joint angles. Do not describe appearance, facial features, hair, clothing or background. Do not invent hidden details. Maximum 70 words.`;
}
export function poseEditPrompt(preset, pose, userPrompt) {
  const instruction = `${pose.trim()} ${identity} ${preset.keep}`;
  return userPrompt.trim() === preset.prompt ? instruction : `${instruction}\n利用者の追加指示：${userPrompt.trim()}`;
}
